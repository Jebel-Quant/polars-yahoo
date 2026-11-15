# polars-yahoo

Minimal Yahoo Finance price history, returned as [polars](https://pola.rs) DataFrames.
No pandas, two dependencies (`polars`, `curl_cffi`), and three things only:
OHLCV bars, dividends and splits.

Inspired by [yfinance](https://github.com/ranaroussi/yfinance); written from scratch.
Not affiliated with Yahoo — check Yahoo's terms of use before relying on the data.

## Install

```bash
uv add polars-yahoo
```

## Use

```python
import polars_yahoo as yh

# One symbol
yh.Ticker("AAPL").history(period="1y")  # daily bars
yh.Ticker("AAPL").history(start="2024-01-01", end="2024-07-01", interval="1wk")
yh.Ticker("SAP.DE").history(period="5d", interval="5m")  # intraday

# Several symbols, fetched concurrently, stacked into one long frame
yh.download(["AAPL", "MSFT", "SAP.DE"], period="6mo")

# Corporate actions (full history)
yh.Ticker("AAPL").dividends()
yh.Ticker("AAPL").splits()
```

## Output

Price frames are long-format, one row per ticker and bar:

| column | type | notes |
|---|---|---|
| `ticker` | `String` | |
| `date` | `Date` | daily and longer intervals; the exchange-local calendar date |
| `timestamp` | `Datetime(us, tz)` | intraday intervals instead of `date`; exchange time zone |
| `open`, `high`, `low`, `close` | `Float64` | as Yahoo reports them, unadjusted |
| `adj_close` | `Float64` | Yahoo's split- and dividend-adjusted close; null for intraday |
| `volume` | `Int64` | |

`download` converts intraday timestamps to UTC when the symbols trade in
different time zones, because a polars column holds one time zone.

To adjust OHLC yourself:

```python
df.with_columns((pl.col("open", "high", "low", "close") * pl.col("adj_close") / pl.col("close")))
```

`dividends()` returns `ticker, date, dividend`; `splits()` returns
`ticker, date, numerator, denominator, ratio`.

## Arguments

- `period`: `1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max` (ignored when `start` is given)
- `interval`: `1m 2m 5m 15m 60m 90m 1h 1d 5d 1wk 1mo 3mo`. Yahoo caps how far back
  intraday data goes (about 7 days for `1m`, 60 days for other minute bars, 730 days for hourly).
- `start` / `end`: `date`, `datetime` or ISO string. Naive values are UTC; `end` is exclusive.
- `prepost`: include pre- and post-market bars (intraday only).

## Errors

All errors subclass `polars_yahoo.YahooError`:
`TickerNotFoundError` (unknown or delisted symbol) and `RateLimitError`
(HTTP 429 persisted after retries with exponential backoff).
`download` skips failing symbols with a warning unless `raise_errors=True`.

## What is deliberately missing

No price repair heuristics, no quote/info/fundamentals, no caching, no
async API. The scope is kept small on purpose.

## Development

```bash
uv sync
uv run pytest            # offline tests, network is faked
uv run pytest -m live    # one round trip against Yahoo
```
