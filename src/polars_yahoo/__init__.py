"""Minimal Yahoo Finance price history as polars DataFrames.

>>> import polars_yahoo as yh
>>> yh.Ticker("aapl")
Ticker('AAPL')
"""

from __future__ import annotations

from ._chart import INTERVALS, PERIODS
from ._download import download
from ._ticker import Ticker
from .exceptions import NetworkError, RateLimitError, ResponseError, TickerNotFoundError, YahooError

__all__ = [
    "INTERVALS",
    "PERIODS",
    "NetworkError",
    "RateLimitError",
    "ResponseError",
    "Ticker",
    "TickerNotFoundError",
    "YahooError",
    "download",
]

__version__ = "0.3.0"
