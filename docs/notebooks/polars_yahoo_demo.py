# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo>=0.25",
#     "polars-yahoo",
#     "altair>=5",
#     # Installed anyway via polars-yahoo, but imported directly here: `marimo export --sandbox`
#     # (used by the book) pins every directly imported package missing from this list.
#     "polars>=1.20",
#     "curl_cffi>=0.17",
# ]
#
# [tool.uv.sources]
# polars-yahoo = { path = "../..", editable = true }
# ///

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import warnings

    import altair as alt
    import marimo as mo
    import polars as pl

    import polars_yahoo as yh

    return alt, mo, pl, warnings, yh


@app.cell
def _(mo, yh):
    mo.md(
        f"""
        # polars-yahoo {yh.__version__}: a guided tour

        `polars-yahoo` fetches Yahoo Finance price history straight into polars
        DataFrames. It does three things: **OHLCV bars**, **dividends** and **splits**.

        This notebook works through all of it:

        1. Periods and intervals
        2. One symbol with `Ticker`: history, explicit windows, `period="max"`, intraday bars
        3. Adjusted prices
        4. Dividends and splits
        5. Several symbols with `download`, including mixed time zones
        6. An interactive explorer
        7. Errors: what is raised, what is skipped, what is rejected up front
        8. Request settings, retries and bringing your own session

        Every cell here hits Yahoo live, so the numbers change from run to run.
        This package is not affiliated with Yahoo, so check Yahoo's terms before
        relying on the data.
        """
    )
    return


@app.cell
def _(mo, pl, yh):
    _choices = (
        pl.DataFrame(
            {
                "kind": ["period"] * len(yh.PERIODS) + ["interval"] * len(yh.INTERVALS),
                "value": [*yh.PERIODS, *yh.INTERVALS],
            }
        )
        .group_by("kind", maintain_order=True)
        .agg(pl.col("value").str.join("  "))
    )
    mo.vstack(
        [
            mo.md(
                """
                ## 1. Periods and intervals

                A request names a **range**, either a named `period` or an explicit
                `start`/`end` window, and a bar **interval**. Both sets are exported:
                """
            ),
            _choices,
            mo.md(
                """
                Minute and hour bars are **intraday**. They carry a time-zone-aware
                `timestamp`. Everything from `1d` up carries an exchange-local `date`.
                """
            ),
        ]
    )
    return


@app.cell
def _(mo, yh):
    aapl = yh.Ticker("  aapl ")
    mo.md(
        f"""
        ## 2. One symbol: `Ticker`

        Symbols are stripped and upper-cased, so `yh.Ticker("  aapl ")` gives
        `{aapl!r}` with `symbol == {aapl.symbol!r}`.
        Creating a `Ticker` sends no request; each method call fetches what it needs.
        """
    )
    return (aapl,)


@app.cell
def _(aapl, mo):
    recent = aapl.history()
    mo.vstack(
        [
            mo.md(
                f"""
                ### Default: one month of daily bars

                `aapl.history()` means `period="1mo", interval="1d"`. The frame is long,
                with one row per bar, and has **{recent.height} rows**:
                """
            ),
            recent,
        ]
    )
    return


@app.cell
def _(aapl, mo):
    weekly = aapl.history(start="2024-01-01", end="2024-07-01", interval="1wk")
    mo.vstack(
        [
            mo.md(
                """
                ### An explicit window

                `start` and `end` take a `date`, a `datetime` or an ISO string. Naive
                values are read as UTC, `end` is **exclusive**, and a `start` wins over
                `period`. Here are weekly bars for the first half of 2024:
                """
            ),
            weekly,
        ]
    )
    return


@app.cell
def _(aapl, mo, pl):
    full = aapl.history(period="max")
    _span = full.select(
        pl.col("date").min().alias("first bar"),
        pl.col("date").max().alias("last bar"),
        pl.len().alias("bars"),
        pl.col("date").diff().dt.total_days().median().alias("median days between bars"),
    )
    mo.vstack(
        [
            mo.md(
                """
                ### The whole history: `period="max"`

                For `range=max`, Yahoo returns **monthly** bars whatever interval
                you ask for. So polars-yahoo sends `max` as an explicit window starting
                1900-01-01, and daily bars stay daily. The median gap below is 1 day
                (weekends add the occasional larger one), not about 30:
                """
            ),
            _span,
        ]
    )
    return (full,)


@app.cell
def _(mo, yh):
    sap = yh.Ticker("SAP.DE").history(period="5d", interval="15m")
    mo.vstack(
        [
            mo.md(
                f"""
                ### Intraday bars

                Intraday frames carry a `timestamp` in the **exchange's own time zone**.
                For SAP on Xetra that is `{sap.schema["timestamp"]}`. Yahoo provides no
                adjusted close for intraday bars, so `adj_close` is null.
                Yahoo also limits how far back intraday data goes: about 7 days for
                `1m`, 60 days for other minute bars and 730 days for hourly bars.
                """
            ),
            sap.head(8),
        ]
    )
    return


@app.cell
def _(mo, yh):
    _regular = yh.Ticker("AAPL").history(period="5d", interval="15m")
    _extended = yh.Ticker("AAPL").history(period="5d", interval="15m", prepost=True)
    mo.md(
        f"""
        ### Pre- and post-market: `prepost=True`

        With the same request, the regular session alone gives **{_regular.height}**
        15-minute bars, and adding pre- and post-market trading gives
        **{_extended.height}**. The flag only affects intraday intervals.
        """
    )
    return


@app.cell
def _(full, mo, pl):
    _adjusted = full.with_columns(
        (pl.col("open", "high", "low", "close") * pl.col("adj_close") / pl.col("close")).name.suffix("_adj")
    ).select("date", "close", "adj_close", "close_adj", "open", "open_adj")
    mo.vstack(
        [
            mo.md(
                """
                ## 3. Adjusted prices

                `open`, `high`, `low` and `close` are **unadjusted**, as Yahoo reports
                them. Yahoo's split- and dividend-adjusted close sits next to them as
                `adj_close`. To adjust the whole bar, scale it by `adj_close / close`.
                On AAPL's earliest bars the gap is large, because of every split and
                dividend since (see section 4):
                """
            ),
            _adjusted.head(5),
        ]
    )
    return


@app.cell
def _(aapl, mo):
    dividends = aapl.dividends()
    splits = aapl.splits()
    mo.vstack(
        [
            mo.md(
                f"""
                ## 4. Dividends and splits

                Both return the **full history**. They share a single request, which
                is cached on the `Ticker` (create a new `Ticker` to refetch).
                AAPL has paid **{dividends.height}** dividends:
                """
            ),
            dividends.tail(5),
            mo.md("…and split its stock this many times:"),
            splits,
        ]
    )
    return


@app.cell
def _(mo, pl, yh):
    stacked = yh.download("msft, aapl msft SAP.DE", period="3mo")
    mo.vstack(
        [
            mo.md(
                """
                ## 5. Several symbols: `download`

                `download` takes a list or a space/comma-separated string. It
                upper-cases and de-duplicates the symbols, fetches them **concurrently**
                (8 workers by default, `max_workers=` to change), and stacks them into
                one long frame sorted by ticker, then time. `"msft, aapl msft SAP.DE"`
                becomes three symbols:
                """
            ),
            stacked.group_by("ticker", maintain_order=True).agg(
                pl.len().alias("bars"), pl.col("date").min().alias("from"), pl.col("date").max().alias("to")
            ),
            mo.md("Long is easy to pivot wide when you need one column per symbol:"),
            stacked.pivot("ticker", index="date", values="close").sort("date").tail(5),
        ]
    )
    return


@app.cell
def _(mo, yh):
    _mixed = yh.download(["AAPL", "SAP.DE"], period="5d", interval="1h")
    _same = yh.download(["AAPL", "MSFT"], period="5d", interval="1h")
    mo.md(
        f"""
        ### Intraday across time zones

        A polars column holds a single time zone. So when intraday symbols trade in
        **different** zones, `download` converts every timestamp to UTC:

        - `AAPL` + `SAP.DE` → `{_mixed.schema["timestamp"]}`
        - `AAPL` + `MSFT` → `{_same.schema["timestamp"]}` (one zone, kept as is)
        """
    )
    return


@app.cell
def _(mo, yh):
    symbols = mo.ui.text(value="AAPL, MSFT, NVDA, SAP.DE", label="Symbols", full_width=True)
    period = mo.ui.dropdown(options=list(yh.PERIODS), value="1y", label="Period")
    interval = mo.ui.dropdown(options=["1d", "1wk", "1mo"], value="1d", label="Interval")
    mo.vstack(
        [
            mo.md(
                """
                ## 6. Explore

                Pick symbols and a range. The chart shows each symbol's adjusted close,
                rebased to 100 at the start of the window, so different price levels
                can be compared.
                """
            ),
            mo.hstack([symbols, period, interval], widths=[3, 1, 1]),
        ]
    )
    return interval, period, symbols


@app.cell
def _(alt, interval, mo, period, pl, symbols, warnings, yh):
    with warnings.catch_warnings(record=True) as _caught:
        warnings.simplefilter("always")
        explored = yh.download(symbols.value, period=period.value, interval=interval.value)
    _rebased = explored.with_columns(
        (100 * pl.col("adj_close") / pl.col("adj_close").first().over("ticker")).alias("rebased")
    )
    _chart = (
        alt.Chart(_rebased)
        .mark_line()
        .encode(
            x=alt.X("date:T", title=None),
            y=alt.Y("rebased:Q", title="Adjusted close, rebased to 100", scale=alt.Scale(zero=False)),
            color=alt.Color("ticker:N", title=None),
            tooltip=["ticker", "date", alt.Tooltip("close:Q", format=".2f"), alt.Tooltip("rebased:Q", format=".1f")],
        )
        .properties(height=360)
    )
    _skipped = [mo.callout(mo.md(f"`{w.message}`"), kind="warn") for w in _caught]
    mo.vstack([*_skipped, _chart])
    return


@app.cell
def _(mo, pl, warnings, yh):
    with warnings.catch_warnings(record=True) as _caught:
        warnings.simplefilter("always")
        survivors = yh.download(["AAPL", "NOSUCHTICKER123"], period="5d")
    mo.vstack(
        [
            mo.md(
                """
                ## 7. Errors

                Every error the package raises subclasses `polars_yahoo.YahooError`:

                | error | when |
                |---|---|
                | `TickerNotFoundError` | unknown or delisted symbol |
                | `RateLimitError` | HTTP 429 persisted after retries with exponential backoff |
                | `NetworkError` | no HTTP response at all, after retries: timeout, DNS or connection failure |
                | `ResponseError` | any other unusable response: a 5xx after retries, or malformed chart data |

                HTTP 429, any 5xx and transport failures are retried before an error is
                raised (see section 8). Anything else, such as an unknown symbol, fails at once.

                ### `download` skips a failing symbol and keeps the rest

                A symbol that fails produces a warning, and the others still come back:
                """
            ),
            *[mo.callout(mo.md(f"`{w.message}`"), kind="warn") for w in _caught],
            survivors.select(pl.col("ticker").unique()),
        ]
    )
    return


@app.cell
def _(mo, yh):
    _outcomes = []
    for _label, _call in [
        ('Ticker("NOSUCHTICKER123").history()', lambda: yh.Ticker("NOSUCHTICKER123").history()),
        (
            'download(["AAPL", "NOSUCHTICKER123"], raise_errors=True)',
            lambda: yh.download(["AAPL", "NOSUCHTICKER123"], raise_errors=True),
        ),
        (
            'Ticker("AAPL", timeout=0.001, retries=0).history()',
            lambda: yh.Ticker("AAPL", timeout=0.001, retries=0).history(),
        ),
    ]:
        try:
            _call()
            _outcomes.append({"call": _label, "raised": "nothing", "message": ""})
        except yh.YahooError as _exc:
            _outcomes.append({"call": _label, "raised": type(_exc).__name__, "message": str(_exc)})
    mo.vstack(
        [
            mo.md(
                """
                ### Raising instead

                A single `Ticker` raises. So does `download(..., raise_errors=True)`.
                A one-millisecond timeout shows what a transport failure looks like.
                It is retried like any transport failure, so `retries=0` makes it fail
                straight away instead of after three retries:
                """
            ),
            mo.ui.table(_outcomes, selection=None),
        ]
    )
    return


@app.cell
def _(mo, yh):
    _rejected = []
    for _label, _call in [
        ('history(interval="30m")', lambda: yh.Ticker("AAPL").history(interval="30m")),
        ('history(period="max", interval="5m")', lambda: yh.Ticker("AAPL").history(period="max", interval="5m")),
        ('history(end="2024-07-01")', lambda: yh.Ticker("AAPL").history(end="2024-07-01")),
        (
            'history(start="2024-07-01", end="2024-01-01")',
            lambda: yh.Ticker("AAPL").history(start="2024-07-01", end="2024-01-01"),
        ),
        ('Ticker("AAPL", retries=-1)', lambda: yh.Ticker("AAPL", retries=-1)),
        ('download(" , ")', lambda: yh.download(" , ")),
    ]:
        try:
            _call()
            _rejected.append({"call": _label, "message": "accepted"})
        except ValueError as _exc:
            _rejected.append({"call": _label, "message": str(_exc)})
    mo.vstack(
        [
            mo.md(
                """
                ### Bad arguments fail before any request

                Invalid arguments raise `ValueError` up front. No request is sent, and
                `download` checks them once, before fetching any symbol:
                """
            ),
            mo.ui.table(_rejected, selection=None),
        ]
    )
    return


@app.cell
def _(yh):
    from curl_cffi import requests as curl_requests

    class RecordingSession:
        """Wrap a browser-impersonating session and record every request it sends."""

        def __init__(self) -> None:
            """Open the underlying session and start with an empty log."""
            self._inner = curl_requests.Session(impersonate="chrome")
            self.log: list[dict] = []

        def get(self, url: str, *, params: dict, timeout: float) -> yh.Response:
            """Send the request, then note what was asked for and what came back."""
            response = self._inner.get(url, params=params, timeout=timeout)
            self.log.append({"symbol": url.rsplit("/", 1)[-1], **params, "status": response.status_code})
            return response

    return (RecordingSession,)


@app.cell
def _(RecordingSession, mo, pl, yh):
    recorder: yh.Session = RecordingSession()
    _msft = yh.Ticker("MSFT", session=recorder, timeout=10.0, retries=5, backoff=0.5)
    _msft.history(period="max")
    _msft.dividends()
    _msft.splits()
    yh.download(["AAPL", "NVDA"], period="5d", session=recorder)
    mo.vstack(
        [
            mo.md(
                """
                ## 8. Request settings and your own session

                `Ticker` and `download` both take keyword-only `timeout` (seconds per
                request, default 30), `retries` (how many times a failed request is
                retried, default 3) and `backoff` (the base wait between retries,
                doubled each time, default 1 second). HTTP 429, any 5xx and transport
                failures are retried; other responses, such as a 404, are not.

                They also accept `session=`: anything with a `requests`-style
                `get(url, *, params, timeout)`. That shape is exported as the
                `yh.Session` protocol (its responses as `yh.Response`), so a type
                checker can verify your own session. Without one, requests go through a
                shared pool of browser-impersonating `curl_cffi` sessions that is
                reused across calls and never lends one session to two threads.
                Passing your own session makes `download` fetch one symbol after
                another, since a single session must not be shared across threads.

                The session below wraps `curl_cffi` and records what was sent:
                """
            ),
            pl.DataFrame(recorder.log),
            mo.md(
                """
                Reading the log:

                - `history(period="max")` went out as an explicit `period1`/`period2`
                  window, not `range=max`.
                - `dividends()` and `splits()` together cost **one** request: the
                  full-history `3mo` call.
                - `download` with a session sent its two symbols one after the other.
                """
            ),
        ]
    )
    return


@app.cell
def _(RecordingSession, mo, pl, yh):
    class FlakySession(RecordingSession):
        """Fail the first request with a simulated connection reset, then behave normally."""

        def get(self, url: str, *, params: dict, timeout: float) -> yh.Response:
            """Raise once, as a dropped connection would, then delegate to the recording session."""
            if not self.log:
                self.log.append({"symbol": url.rsplit("/", 1)[-1], "status": None, "note": "connection reset"})
                raise ConnectionResetError  # an OSError, like curl_cffi's transport errors
            return super().get(url, params=params, timeout=timeout)

    flaky = FlakySession()
    _bars = yh.Ticker("AAPL", session=flaky, backoff=0.2).history(period="5d")
    mo.vstack(
        [
            mo.md(
                f"""
                ### Retries in action

                This session drops its first connection on purpose. `history()` waits
                `backoff` (0.2 s here), tries again and gets its **{_bars.height}** bars,
                so the caller never sees the failure. Only after `retries` failed retries
                in a row would it raise `NetworkError`. The log shows both attempts:
                """
            ),
            pl.DataFrame(flaky.log).select("symbol", "status", pl.col("note").fill_null("answered")),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
