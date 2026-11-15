"""HTTP access to Yahoo's chart endpoint.

A browser-impersonating ``curl_cffi`` session is used because Yahoo
rate-limits clients that don't look like a browser. Sessions are kept per
thread, since a ``curl_cffi`` session must not be shared across threads.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

from curl_cffi import requests as _requests

from .exceptions import NetworkError, RateLimitError, ResponseError, TickerNotFoundError

CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"

_local = threading.local()


class Response(Protocol):
    """The subset of a response object this package relies on."""

    status_code: int

    def json(self) -> Any:
        """Decode the body as JSON."""
        ...


class Session(Protocol):
    """Anything with a ``requests``-style ``get`` — lets tests inject a fake."""

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> Response:
        """Issue a GET request."""
        ...


def default_session() -> Session:
    """Return this thread's shared browser-impersonating session."""
    session = getattr(_local, "session", None)
    if session is None:
        session = _requests.Session(impersonate="chrome")
        _local.session = session
    return session


def get_chart(
    ticker: str,
    params: dict[str, Any],
    *,
    session: Session | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 1.0,
) -> dict[str, Any]:
    """Fetch ``/v8/finance/chart/{ticker}`` and return its single ``result`` object.

    Retries HTTP 429 with exponential backoff, then raises :class:`RateLimitError`.
    Raises :class:`TickerNotFoundError` when Yahoo reports no data for the symbol,
    and :class:`NetworkError` when no HTTP response arrives at all.
    """
    session = session or default_session()
    url = CHART_URL.format(ticker=ticker)
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
        except OSError as exc:  # curl_cffi's RequestException is an OSError
            raise NetworkError(ticker, str(exc)) from exc
        if response.status_code != 429:
            break
        if attempt < retries:
            time.sleep(backoff * 2**attempt)
    else:
        raise RateLimitError(ticker, retries)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ResponseError(ticker, response.status_code, "body is not JSON") from exc

    chart = payload.get("chart") or {}
    error = chart.get("error")
    results = chart.get("result")
    if error or not results:
        reason = (error or {}).get("description")
        if response.status_code in (200, 404):
            raise TickerNotFoundError(ticker, reason)
        raise ResponseError(ticker, response.status_code, reason or "unexpected response")
    return results[0]
