"""Build chart-request parameters and turn chart responses into polars frames.

Everything here is pure (no I/O) so it can be tested against recorded payloads.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
INTRADAY_INTERVALS = ("1m", "2m", "5m", "15m", "60m", "90m", "1h")
DAILY_INTERVALS = ("1d", "5d", "1wk", "1mo", "3mo")
INTERVALS = INTRADAY_INTERVALS + DAILY_INTERVALS

# For range=max Yahoo silently returns monthly bars whatever interval was asked
# for, so "max" is sent as an explicit window starting here (1900-01-01 UTC).
MAX_START = -2208988800

PRICE_COLUMNS = ("open", "high", "low", "close", "adj_close")

DateLike = dt.date | dt.datetime | str


def is_intraday(interval: str) -> bool:
    """Return True for minute/hour bars, which carry a timestamp rather than a date.

    >>> is_intraday("5m"), is_intraday("1d")
    (True, False)
    """
    return interval in INTRADAY_INTERVALS


def _to_epoch(value: DateLike) -> int:
    """Convert a date, datetime or ISO string to Unix seconds.

    Naive values are taken as UTC; a bare date means midnight UTC.

    >>> _to_epoch("2024-01-02")
    1704153600
    """
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if not isinstance(value, dt.datetime):
        value = dt.datetime(value.year, value.month, value.day)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return int(value.timestamp())


def build_params(
    *,
    period: str | None = "1mo",
    interval: str = "1d",
    start: DateLike | None = None,
    end: DateLike | None = None,
    prepost: bool = False,
) -> dict[str, Any]:
    """Validate the arguments and translate them into chart query parameters.

    Either ``period`` or ``start`` (optionally with ``end``) selects the range;
    ``start``/``end`` win, and ``end`` defaults to now. ``end`` is exclusive.

    >>> build_params(period="5d")["range"]
    '5d'
    >>> params = build_params(period="max")  # sent as an explicit window, not range=max
    >>> params["period1"], "range" in params
    (-2208988800, False)
    """
    if interval not in INTERVALS:
        hint = " (Yahoo returns unreliable 30m bars; use 15m)" if interval == "30m" else ""
        raise ValueError(f"interval must be one of {', '.join(INTERVALS)}, got {interval!r}{hint}")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom

    params: dict[str, Any] = {
        "interval": interval,
        "includePrePost": str(prepost).lower(),
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }
    if start is not None:
        params["period1"] = _to_epoch(start)
        params["period2"] = _to_epoch(end) if end is not None else int(dt.datetime.now(dt.UTC).timestamp())
        if params["period2"] <= params["period1"]:
            raise ValueError("end must be after start")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    elif end is not None:
        raise ValueError("end requires start")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    else:
        if period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(PERIODS)}, got {period!r}")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
        if period == "max" and interval not in ("1mo", "3mo"):
            if is_intraday(interval):
                raise ValueError(  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
                    f"period='max' is not available for intraday interval {interval!r}; "
                    "Yahoo only keeps recent intraday bars, so pass a shorter period or a start"
                )
            params["period1"] = MAX_START
            params["period2"] = int(dt.datetime.now(dt.UTC).timestamp())
        else:
            params["range"] = period
    return params


def _price_schema(intraday: bool, tz: str) -> dict[str, pl.DataType]:
    """Return the price-frame schema: ``date`` for daily bars, a tz-aware ``timestamp`` for intraday."""
    time_col: dict[str, pl.DataType] = (
        {"timestamp": pl.Datetime("us", time_zone=tz)} if intraday else {"date": pl.Date()}
    )
    return {
        "ticker": pl.String(),
        **time_col,
        **{c: pl.Float64() for c in PRICE_COLUMNS},
        "volume": pl.Int64(),
    }


def _local_times(timestamps: list[int], tz: str) -> pl.Series:
    """Convert Unix seconds to microsecond datetimes in the exchange time zone."""
    return (
        pl.from_epoch(pl.Series("timestamp", timestamps, dtype=pl.Int64), time_unit="s")
        .cast(pl.Datetime("us"))
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone(tz)
    )


def parse_prices(ticker: str, result: dict[str, Any], interval: str) -> pl.DataFrame:
    """Turn one chart ``result`` into a long price frame.

    Columns: ``ticker``, then ``date`` (daily and longer bars, exchange-local
    calendar date) or ``timestamp`` (intraday, exchange time zone), then
    ``open, high, low, close, adj_close, volume``. ``adj_close`` is null for
    intraday bars because Yahoo does not provide it there. Rows with no prices
    at all are dropped, and for daily bars a duplicated trailing live bar is
    collapsed into one row per date.

    >>> result = {
    ...     "meta": {"exchangeTimezoneName": "America/New_York"},
    ...     "timestamp": [1704205800],
    ...     "indicators": {
    ...         "quote": [
    ...             {"open": [187.15], "high": [188.44], "low": [183.89], "close": [185.64], "volume": [82488700]}
    ...         ],
    ...         "adjclose": [{"adjclose": [184.73]}],
    ...     },
    ... }
    >>> frame = parse_prices("AAPL", result, "1d")
    >>> frame.columns
    ['ticker', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
    >>> frame.row(0)
    ('AAPL', datetime.date(2024, 1, 2), 187.15, 188.44, 183.89, 185.64, 184.73, 82488700)
    """
    meta = result.get("meta") or {}
    tz = meta.get("exchangeTimezoneName") or "UTC"
    intraday = is_intraday(interval)
    schema = _price_schema(intraday, tz)

    timestamps = result.get("timestamp") or []
    if not timestamps:
        return pl.DataFrame(schema=schema)

    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjclose = (indicators.get("adjclose") or [{}])[0].get("adjclose")
    n = len(timestamps)

    def column(values: list[Any] | None) -> list[Any]:
        """Return ``values``, or a column of nulls when Yahoo omitted the field."""
        return values if values is not None else [None] * n

    frame = pl.DataFrame(
        {
            "ticker": [ticker] * n,
            "timestamp": _local_times(timestamps, tz),
            "open": column(quote.get("open")),
            "high": column(quote.get("high")),
            "low": column(quote.get("low")),
            "close": column(quote.get("close")),
            "adj_close": column(adjclose),
            "volume": column(quote.get("volume")),
        },
        schema_overrides=dict.fromkeys(PRICE_COLUMNS, pl.Float64) | {"volume": pl.Int64},
    ).filter(~pl.all_horizontal(pl.col("open", "high", "low", "close").is_null()))

    if not intraday:
        frame = (
            frame.with_columns(pl.col("timestamp").dt.date().alias("date"))
            .drop("timestamp")
            .unique(subset="date", keep="last", maintain_order=True)
        )
    time_col = "timestamp" if intraday else "date"
    return frame.select(list(schema)).sort(time_col)


def parse_dividends(ticker: str, result: dict[str, Any]) -> pl.DataFrame:
    """Return ``ticker, date, dividend`` rows from a chart ``result``'s events.

    >>> events = {"dividends": {"1715347800": {"date": 1715347800, "amount": 0.25}}}
    >>> parse_dividends("AAPL", {"meta": {"exchangeTimezoneName": "America/New_York"}, "events": events}).row(0)
    ('AAPL', datetime.date(2024, 5, 10), 0.25)
    """
    tz = (result.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
    events = list(((result.get("events") or {}).get("dividends") or {}).values())
    schema = {"ticker": pl.String(), "date": pl.Date(), "dividend": pl.Float64()}
    if not events:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(
        {
            "ticker": [ticker] * len(events),
            "date": _local_times([e["date"] for e in events], tz).dt.date(),
            "dividend": [e.get("amount") for e in events],
        },
        schema=schema,
    ).sort("date")


def parse_splits(ticker: str, result: dict[str, Any]) -> pl.DataFrame:
    """Return ``ticker, date, numerator, denominator, ratio`` rows from a chart ``result``.

    >>> events = {"splits": {"1598880600": {"date": 1598880600, "numerator": 4, "denominator": 1}}}
    >>> parse_splits("AAPL", {"meta": {"exchangeTimezoneName": "America/New_York"}, "events": events}).row(0)
    ('AAPL', datetime.date(2020, 8, 31), 4.0, 1.0, 4.0)
    """
    tz = (result.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
    events = list(((result.get("events") or {}).get("splits") or {}).values())
    schema = {
        "ticker": pl.String(),
        "date": pl.Date(),
        "numerator": pl.Float64(),
        "denominator": pl.Float64(),
    }
    if not events:
        return pl.DataFrame(schema=schema).with_columns(ratio=pl.lit(None, pl.Float64))
    return (
        pl.DataFrame(
            {
                "ticker": [ticker] * len(events),
                "date": _local_times([e["date"] for e in events], tz).dt.date(),
                "numerator": [e.get("numerator") for e in events],
                "denominator": [e.get("denominator") for e in events],
            },
            schema=schema,
        )
        .with_columns(ratio=pl.col("numerator") / pl.col("denominator"))
        .sort("date")
    )
