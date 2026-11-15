"""Concurrent multi-symbol price download."""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import polars as pl

from . import _chart, _http
from ._chart import DateLike
from ._ticker import Ticker
from .exceptions import YahooError


def _normalise(tickers: str | Iterable[str]) -> list[str]:
    """Split, upper-case and de-duplicate symbols, keeping their order."""
    items = re.split(r"[\s,]+", tickers) if isinstance(tickers, str) else list(tickers)
    return list(dict.fromkeys(s.strip().upper() for s in items if s and s.strip()))


def _collect(symbols: list[str], outcomes: list[pl.DataFrame | YahooError], raise_errors: bool) -> list[pl.DataFrame]:
    """Keep the frames that arrived; warn about (or raise) the failures."""
    frames: list[pl.DataFrame] = []
    for symbol, outcome in zip(symbols, outcomes, strict=True):
        if isinstance(outcome, YahooError):
            if raise_errors:
                raise outcome
            # stacklevel=3: attribute the warning to download()'s caller.
            warnings.warn(f"skipping {symbol}: {outcome}", stacklevel=3)
        else:
            frames.append(outcome)
    return frames


def _stack(frames: list[pl.DataFrame], interval: str) -> pl.DataFrame:
    """Stack per-symbol frames into one, sorted by ticker then time.

    Intraday frames on different time zones are converted to UTC first, since a
    polars column holds a single time zone.
    """
    if not frames:
        return _chart.empty_prices(interval)
    if _chart.is_intraday(interval):
        zones = {dtype.time_zone for f in frames if isinstance(dtype := f.schema["timestamp"], pl.Datetime)}
        if len(zones) > 1:
            frames = [f.with_columns(pl.col("timestamp").dt.convert_time_zone("UTC")) for f in frames]
    return pl.concat(frames, how="vertical").sort("ticker", _chart.time_column(interval))


def download(
    tickers: str | Iterable[str],
    period: str | None = "1mo",
    interval: str = "1d",
    start: DateLike | None = None,
    end: DateLike | None = None,
    prepost: bool = False,
    *,
    max_workers: int = 8,
    raise_errors: bool = False,
    session: _http.Session | None = None,
    timeout: float = _http.TIMEOUT,
    retries: int = _http.RETRIES,
    backoff: float = _http.BACKOFF,
) -> pl.DataFrame:
    """Download bars for several symbols into one long frame.

    ``tickers`` is a list or a space/comma-separated string. The result has the
    same columns as :meth:`Ticker.history`, stacked and sorted by ticker then
    time. Intraday timestamps of symbols on different time zones are
    converted to UTC so they can share one column.

    A symbol that fails is skipped with a warning, or raised when
    ``raise_errors`` is true. Passing ``session`` runs the requests one after
    another, because a single session must not be shared across threads.
    ``timeout``, ``retries`` and ``backoff`` apply to every request, as for
    :class:`Ticker`.
    """
    symbols = _normalise(tickers)
    if not symbols:
        raise ValueError("no tickers given")  # noqa: TRY003 - argument validation; a ValueError with context is the idiom
    # Validate once, up front, so bad arguments fail before any request.
    _chart.build_params(period=period, interval=interval, start=start, end=end, prepost=prepost)
    _http.check_request_settings(timeout, retries, backoff)

    def fetch(symbol: str) -> pl.DataFrame | YahooError:
        """Fetch one symbol, returning the error instead of raising so other symbols still complete."""
        try:
            ticker = Ticker(symbol, session=session, timeout=timeout, retries=retries, backoff=backoff)
            return ticker.history(period, interval, start, end, prepost)
        except YahooError as exc:
            return exc

    workers = 1 if session is not None else max(1, min(max_workers, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(fetch, symbols))

    return _stack(_collect(symbols, outcomes, raise_errors), interval)
