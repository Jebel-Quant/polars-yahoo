"""Exceptions raised by polars-yahoo."""

from __future__ import annotations


class YahooError(Exception):
    """Base class for every error this package raises."""


class TickerNotFoundError(YahooError):
    """Yahoo has no data for the symbol (unknown or delisted)."""

    def __init__(self, ticker: str, reason: str | None = None) -> None:
        """Record the ticker and Yahoo's own explanation, if it gave one."""
        self.ticker = ticker
        self.reason = reason
        super().__init__(f"{ticker}: {reason or 'no data found'}")


class RateLimitError(YahooError):
    """Yahoo kept answering HTTP 429 after all retries."""

    def __init__(self, ticker: str, retries: int) -> None:
        """Record the ticker and how many retries were spent."""
        self.ticker = ticker
        self.retries = retries
        super().__init__(f"{ticker}: Yahoo rate limit (HTTP 429) persisted after {retries} retries")


class ResponseError(YahooError):
    """Yahoo answered with something other than usable chart data."""

    def __init__(self, ticker: str, status: int, reason: str) -> None:
        """Record the ticker, the HTTP status and what was wrong with the response."""
        self.ticker = ticker
        self.status = status
        self.reason = reason
        super().__init__(f"{ticker}: HTTP {status}: {reason}")
