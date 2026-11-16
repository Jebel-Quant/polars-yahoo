"""Single-symbol access: price history, dividends and splits."""

from __future__ import annotations

from collections.abc import Callable
from functools import cached_property
from typing import Any

import polars as pl

from . import _chart, _http
from ._chart import DateLike
from .exceptions import ResponseError

# What a parser raises when a chart result does not have the shape Yahoo documents.
_PARSE_ERRORS = (KeyError, TypeError, AttributeError, pl.exceptions.PolarsError)


class Ticker:
    """One Yahoo Finance symbol.

    ``session`` lets you inject any object with a ``requests``-style ``get``;
    by default a browser-impersonating ``curl_cffi`` session is used.
    ``timeout`` is in seconds per request; an HTTP 429, a 5xx or a transport
    failure is retried up to ``retries`` times, waiting ``backoff * 2**attempt``
    seconds between tries.
    """

    def __init__(
        self,
        symbol: str,
        *,
        session: _http.Session | None = None,
        timeout: float = _http.TIMEOUT,
        retries: int = _http.RETRIES,
        backoff: float = _http.BACKOFF,
    ) -> None:
        """Normalise the symbol (stripped, upper-case) and keep the session and request settings."""
        symbol = symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol must not be empty")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
        _http.check_request_settings(timeout, retries, backoff)
        self.symbol = symbol
        self._session = session
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch one chart result with this ticker's session and request settings."""
        return _http.get_chart(
            self.symbol,
            params,
            session=self._session,
            timeout=self._timeout,
            retries=self._retries,
            backoff=self._backoff,
        )

    def __repr__(self) -> str:
        """Show the symbol."""
        return f"Ticker({self.symbol!r})"

    def _parse(self, parser: Callable[..., pl.DataFrame], *args: Any) -> pl.DataFrame:
        """Run ``parser`` on a chart result, turning a malformed result into :class:`ResponseError`.

        ``get_chart`` only returns a result Yahoo sent as usable data, so the status is 200.
        """
        try:
            return parser(self.symbol, *args)
        except _PARSE_ERRORS as exc:
            raise ResponseError(self.symbol, 200, "malformed chart payload") from exc

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
        result = self._get(params)
        return self._parse(_chart.parse_prices, result, interval)

    @cached_property
    def _events(self) -> dict[str, Any]:
        """Fetch the full-history chart result that carries dividends and splits.

        Yahoo returns every event in range whatever the interval, so the
        coarsest bars are requested to keep the payload small. The result is
        cached on the instance, so ``dividends()`` and ``splits()`` share one
        request; create a new ``Ticker`` to refetch.
        """
        params = _chart.build_params(period="max", interval="3mo")
        return self._get(params)

    def dividends(self) -> pl.DataFrame:
        """Return the full dividend history: ``ticker, date, dividend``."""
        return self._parse(_chart.parse_dividends, self._events)

    def splits(self) -> pl.DataFrame:
        """Return the full split history: ``ticker, date, numerator, denominator, ratio``."""
        return self._parse(_chart.parse_splits, self._events)
