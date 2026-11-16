"""Minimal Yahoo Finance price history as polars DataFrames.

>>> import polars_yahoo as yh
>>> yh.Ticker("aapl")
Ticker('AAPL')
"""

from __future__ import annotations

from ._chart import INTERVALS, PERIODS, DateLike
from ._download import download
from ._http import Response, Session
from ._ticker import Ticker
from .exceptions import NetworkError, RateLimitError, ResponseError, TickerNotFoundError, YahooError

__all__ = [
    "INTERVALS",
    "PERIODS",
    "DateLike",
    "NetworkError",
    "RateLimitError",
    "Response",
    "ResponseError",
    "Session",
    "Ticker",
    "TickerNotFoundError",
    "YahooError",
    "download",
]

__version__ = "0.3.1"
