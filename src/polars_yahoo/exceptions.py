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
