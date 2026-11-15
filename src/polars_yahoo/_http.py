"""HTTP access to Yahoo's chart endpoint.

A browser-impersonating ``curl_cffi`` session is used because Yahoo
rate-limits clients that don't look like a browser. Sessions live in a
module-level pool and outlast a single call, so their connections are reused.
A request takes a session out of the pool and puts it back afterwards, which
means a ``curl_cffi`` session is never used by two threads at once.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol
from urllib.parse import quote

from curl_cffi import requests as _requests

from .exceptions import NetworkError, RateLimitError, ResponseError, TickerNotFoundError

CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"

# Request defaults, shared with Ticker and download so the three cannot drift apart.
TIMEOUT = 30.0
RETRIES = 3
BACKOFF = 1.0


def check_request_settings(timeout: float, retries: int, backoff: float) -> None:
    """Raise ValueError unless ``timeout > 0``, ``retries >= 0`` and ``backoff >= 0``."""
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout!r}")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    if retries < 0:
        raise ValueError(f"retries must not be negative, got {retries!r}")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    if not backoff >= 0:
        raise ValueError(f"backoff must not be negative, got {backoff!r}")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom


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


def _new_session() -> Session:
    """Create a browser-impersonating session."""
    return _requests.Session(impersonate="chrome")


# Idle sessions. It grows to at most the number of requests that ran at the same time.
_pool: queue.SimpleQueue[Session] = queue.SimpleQueue()


@contextmanager
def pooled_session() -> Iterator[Session]:
    """Lend an idle session from the pool, or a new one if none is idle, and return it afterwards."""
    try:
        session = _pool.get_nowait()
    except queue.Empty:
        session = _new_session()
    try:
        yield session
    finally:
        _pool.put(session)


def _send(
    session: Session, url: str, params: dict[str, Any], ticker: str, timeout: float, retries: int, backoff: float
) -> Response:
    """Issue the GET, retrying HTTP 429 with exponential backoff; map transport failures to NetworkError."""
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
        except OSError as exc:  # curl_cffi's RequestException is an OSError
            raise NetworkError(ticker, str(exc)) from exc
        if response.status_code != 429:
            return response
        if attempt < retries:
            time.sleep(backoff * 2**attempt)
    raise RateLimitError(ticker, retries)


_MALFORMED = "malformed chart payload"


def _first_result(ticker: str, status: int, results: Any) -> dict[str, Any]:
    """Return the first chart ``result``, or raise ResponseError when it is not an object."""
    result = results[0] if isinstance(results, list) else None
    if not isinstance(result, dict):
        raise ResponseError(ticker, status, _MALFORMED)
    return result


def _decode(ticker: str, response: Response) -> dict[str, Any]:
    """Return the single chart ``result`` from ``response``, or raise the matching YahooError."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ResponseError(ticker, response.status_code, "body is not JSON") from exc

    chart = (payload.get("chart") or {}) if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise ResponseError(ticker, response.status_code, _MALFORMED)
    error = chart.get("error")
    results = chart.get("result")
    if results and not error:
        return _first_result(ticker, response.status_code, results)
    reason = error.get("description") if isinstance(error, dict) else None
    if response.status_code in (200, 404):
        raise TickerNotFoundError(ticker, reason)
    raise ResponseError(ticker, response.status_code, reason or "unexpected response")


def get_chart(
    ticker: str,
    params: dict[str, Any],
    *,
    session: Session | None = None,
    timeout: float = TIMEOUT,
    retries: int = RETRIES,
    backoff: float = BACKOFF,
) -> dict[str, Any]:
    """Fetch ``/v8/finance/chart/{ticker}`` and return its single ``result`` object.

    Retries HTTP 429 with exponential backoff, then raises :class:`RateLimitError`.
    Raises :class:`TickerNotFoundError` when Yahoo reports no data for the symbol,
    and :class:`NetworkError` when no HTTP response arrives at all.
    """
    # Escape every reserved character so the symbol stays one path segment.
    url = CHART_URL.format(ticker=quote(ticker, safe=""))
    if session is None:
        with pooled_session() as pooled:
            response = _send(pooled, url, params, ticker, timeout, retries, backoff)
    else:
        response = _send(session, url, params, ticker, timeout, retries, backoff)
    return _decode(ticker, response)
