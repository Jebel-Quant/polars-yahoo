"""Cross-check polars-yahoo against yfinance on live Yahoo data.

Both libraries read the same chart endpoint, so on a closed historical window
they must agree. These tests hit the network and are marked ``live``: they are
skipped by default and run with ``pytest -m live``.
"""

from __future__ import annotations

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import polars_yahoo as yh

yf = pytest.importorskip("yfinance")

pytestmark = pytest.mark.live

SYMBOL = "AAPL"
START, END = "2024-01-02", "2024-03-01"
PRICES = ["open", "high", "low", "close", "adj_close"]


def _yf_prices(symbol: str) -> pl.DataFrame:
    """Return yfinance's unadjusted daily bars in polars-yahoo's column layout."""
    hist = yf.Ticker(symbol).history(start=START, end=END, interval="1d", auto_adjust=False, actions=False)
    if hist.empty:
        pytest.skip("yfinance returned no prices (Yahoo throttled it?)")
    return pl.DataFrame(
        {
            "date": list(hist.index.date),
            "open": hist["Open"].tolist(),
            "high": hist["High"].tolist(),
            "low": hist["Low"].tolist(),
            "close": hist["Close"].tolist(),
            "adj_close": hist["Adj Close"].tolist(),
            "volume": hist["Volume"].tolist(),
        },
        schema={"date": pl.Date, **dict.fromkeys(PRICES, pl.Float64), "volume": pl.Int64},
    )


def _yf_events(series, column: str) -> pl.DataFrame:
    """Turn a yfinance event series into ``date, <column>`` rows."""
    if series.empty:
        pytest.skip("yfinance returned no events (Yahoo throttled it?)")
    return pl.DataFrame(
        {"date": list(series.index.date), column: series.tolist()},
        schema={"date": pl.Date, column: pl.Float64},
    ).sort("date")


def test_daily_history_matches_yfinance():
    """Daily OHLCV bars agree on dates, prices and volume."""
    ours = yh.Ticker(SYMBOL).history(period=None, start=START, end=END).drop("ticker")
    theirs = _yf_prices(SYMBOL)

    assert ours["date"].to_list() == theirs["date"].to_list()
    assert ours["volume"].to_list() == theirs["volume"].to_list()
    # yfinance rounds prices to the exchange tick size; adj_close may be refreshed between calls.
    assert_frame_equal(ours.select(PRICES), theirs.select(PRICES), check_exact=False, rel_tol=1e-3)


def test_dividends_match_yfinance():
    """The full dividend history agrees on dates and amounts."""
    ours = yh.Ticker(SYMBOL).dividends().drop("ticker")
    theirs = _yf_events(yf.Ticker(SYMBOL).dividends, "dividend")
    assert_frame_equal(ours, theirs, check_exact=False, rel_tol=1e-6)


def test_splits_match_yfinance():
    """The full split history agrees on dates and ratios."""
    ours = yh.Ticker(SYMBOL).splits().select("date", "ratio")
    theirs = _yf_events(yf.Ticker(SYMBOL).splits, "ratio")
    assert_frame_equal(ours, theirs, check_exact=False, rel_tol=1e-6)
