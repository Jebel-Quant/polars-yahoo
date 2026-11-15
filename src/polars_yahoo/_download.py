"""Concurrent multi-symbol price download."""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import polars as pl

from . import _chart, _http
from ._chart import DateLike
from ._ticker import Ticker
from .exceptions import YahooError


def _normalise(tickers: str | Iterable[str]) -> list[str]:
    """Split, upper-case and de-duplicate symbols, keeping their order."""
    items = re.split(r"[\s,]+", tickers) if isinstance(tickers, str) else list(tickers)
    return list(dict.fromkeys(s.strip().upper() for s in items if s and s.strip()))


def download(
    tickers: str | Iterable[str],
    period: str | None = "1mo",
    interval: str = "1d",
    start: DateLike | None = None,
    end: DateLike | None = None,
    prepost: bool = False,
    *,
    max_workers: int = 8,
    raise_errors: bool = False,
    session: _http.Session | None = None,
) -> pl.DataFrame:
    """Download bars for several symbols into one long frame.

    ``tickers`` is a list or a space/comma-separated string. The result has the
    same columns as :meth:`Ticker.history`, stacked and sorted by ticker then
    time. Intraday timestamps of symbols on different time zones are
    converted to UTC so they can share one column.

    A symbol that fails is skipped with a warning, or raised when
    ``raise_errors`` is true. Passing ``session`` runs the requests one after
    another, because a single session must not be shared across threads.
    """
    symbols = _normalise(tickers)
    if not symbols:
        raise ValueError("no tickers given")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    # Validate once, up front, so bad arguments fail before any request.
    _chart.build_params(period=period, interval=interval, start=start, end=end, prepost=prepost)

    def fetch(symbol: str) -> pl.DataFrame | YahooError:
        """Fetch one symbol, returning the error instead of raising so other symbols still complete."""
        try:
            return Ticker(symbol, session=session).history(period, interval, start, end, prepost)
        except YahooError as exc:
            return exc

    workers = 1 if session is not None else max(1, min(max_workers, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(fetch, symbols))

    frames: list[pl.DataFrame] = []
    for symbol, outcome in zip(symbols, outcomes, strict=True):
        if isinstance(outcome, YahooError):
            if raise_errors:
                raise outcome
            warnings.warn(f"skipping {symbol}: {outcome}", stacklevel=2)
        else:
            frames.append(outcome)

    if not frames:
        return pl.DataFrame(schema=_chart._price_schema(_chart.is_intraday(interval), "UTC"))

    if _chart.is_intraday(interval):
        zones = {dtype.time_zone for f in frames if isinstance(dtype := f.schema["timestamp"], pl.Datetime)}
        if len(zones) > 1:
            frames = [f.with_columns(pl.col("timestamp").dt.convert_time_zone("UTC")) for f in frames]

    time_col = "timestamp" if _chart.is_intraday(interval) else "date"
    return pl.concat(frames, how="vertical").sort("ticker", time_col)
