"""Single-symbol access: price history, dividends and splits."""

from __future__ import annotations

import polars as pl

from . import _chart, _http
from ._chart import DateLike


class Ticker:
    """One Yahoo Finance symbol.

    ``session`` lets you inject any object with a ``requests``-style ``get``;
    by default a browser-impersonating ``curl_cffi`` session is used.
    """

    def __init__(self, symbol: str, *, session: _http.Session | None = None) -> None:
        """Normalise the symbol (stripped, upper-case) and keep the session."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
        self.symbol = symbol
        self._session = session

    def __repr__(self) -> str:
        """Show the symbol."""
        return f"Ticker({self.symbol!r})"

    def history(
        self,
        period: str | None = "1mo",
        interval: str = "1d",
        start: DateLike | None = None,
        end: DateLike | None = None,
        prepost: bool = False,
    ) -> pl.DataFrame:
        """Return OHLCV bars as a long polars frame.

        Daily and longer intervals carry a ``date`` column (exchange-local),
        intraday ones a time-zone-aware ``timestamp``. Prices are as Yahoo
        reports them, unadjusted, with Yahoo's own ``adj_close`` alongside.
        """
        params = _chart.build_params(period=period, interval=interval, start=start, end=end, prepost=prepost)
        result = _http.get_chart(self.symbol, params, session=self._session)
        return _chart.parse_prices(self.symbol, result, interval)

    def _events(self) -> dict:
        """Fetch the full-history chart result that carries dividends and splits.

        Yahoo returns every event in range whatever the interval, so the
        coarsest bars are requested to keep the payload small.
        """
        params = _chart.build_params(period="max", interval="3mo")
        return _http.get_chart(self.symbol, params, session=self._session)

    def dividends(self) -> pl.DataFrame:
        """Return the full dividend history: ``ticker, date, dividend``."""
        return _chart.parse_dividends(self.symbol, self._events())

    def splits(self) -> pl.DataFrame:
        """Return the full split history: ``ticker, date, numerator, denominator, ratio``."""
        return _chart.parse_splits(self.symbol, self._events())
