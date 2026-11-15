"""Tests for the HTTP layer and the public Ticker/download API, against a fake session."""

from __future__ import annotations

import datetime as dt
from urllib.parse import unquote

import polars as pl
import pytest
from conftest import JAN2, JAN3, NOT_FOUND, FakeResponse, FakeSession, chart_payload

import polars_yahoo as yh
from polars_yahoo import _http


class TestHttp:
    """Retry, error mapping and session handling in the HTTP layer."""

    def test_retries_429_then_succeeds(self, monkeypatch):
        """A transient 429 is retried and the later success returned."""
        monkeypatch.setattr(_http.time, "sleep", lambda s: None)
        session = FakeSession({"AAPL": [FakeResponse(429, {}), chart_payload([JAN2])]})
        assert _http.get_chart("AAPL", {}, session=session)["timestamp"] == [JAN2]
        assert len(session.calls) == 2

    def test_gives_up_after_retries(self, monkeypatch):
        """Persistent 429s raise RateLimitError after exponential backoff."""
        sleeps = []
        monkeypatch.setattr(_http.time, "sleep", sleeps.append)
        session = FakeSession({"AAPL": [FakeResponse(429, {})] * 3})
        with pytest.raises(yh.RateLimitError, match="after 2 retries") as info:
            _http.get_chart("AAPL", {}, session=session, retries=2, backoff=0.5)
        assert sleeps == [0.5, 1.0]
        assert info.value.retries == 2

    def test_not_found(self):
        """Yahoo's not-found error maps to TickerNotFoundError naming the ticker."""
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.raises(yh.TickerNotFoundError, match="delisted") as info:
            _http.get_chart("NOPE", {}, session=session)
        assert info.value.ticker == "NOPE"

    def test_non_json_body(self):
        """A non-JSON body raises YahooError with the HTTP status."""
        session = FakeSession({"AAPL": FakeResponse(502, ValueError("not json"))})
        with pytest.raises(yh.YahooError, match="HTTP 502"):
            _http.get_chart("AAPL", {}, session=session)

    def test_server_error_with_json_error(self):
        """A 5xx with a JSON error is a YahooError, not a not-found."""
        session = FakeSession(
            {"AAPL": FakeResponse(500, {"chart": {"result": None, "error": {"description": "boom"}}})}
        )
        with pytest.raises(yh.YahooError, match="boom") as info:
            _http.get_chart("AAPL", {}, session=session)
        assert not isinstance(info.value, yh.TickerNotFoundError)

    def test_transport_error_is_a_network_error(self):
        """A request that never gets a response raises NetworkError, chained to the cause."""
        cause = OSError("Connection timed out")
        session = FakeSession({"AAPL": cause})
        with pytest.raises(yh.NetworkError, match="Connection timed out") as info:
            _http.get_chart("AAPL", {}, session=session)
        assert isinstance(info.value, yh.YahooError)
        assert info.value.ticker == "AAPL"
        assert info.value.__cause__ is cause

    def test_default_session_is_per_thread(self):
        """Each thread gets its own reused session."""
        import threading

        seen = []
        t = threading.Thread(target=lambda: seen.append(_http.default_session()))
        t.start()
        t.join()
        assert _http.default_session() is _http.default_session()
        assert seen[0] is not _http.default_session()

    @pytest.mark.parametrize("symbol", ["A/B", "A?range=max#", "^GSPC"])
    def test_symbol_is_one_escaped_path_segment(self, symbol):
        """Reserved characters in a symbol are percent-encoded and cannot alter the request URL."""
        urls = []

        class Recorder:
            """Record the requested URL and answer with a valid chart."""

            def get(self, url, *, params, timeout):
                """Keep the URL; return one bar."""
                urls.append(url)
                return FakeResponse(200, chart_payload([JAN2]))

        _http.get_chart(symbol, {}, session=Recorder())
        base, segment = urls[0].rsplit("/", 1)
        assert base == "https://query2.finance.yahoo.com/v8/finance/chart"
        assert not set("/?#") & set(segment)
        assert unquote(segment) == symbol

    def test_index_symbol_round_trips(self):
        """A ``^``-style index symbol is fetched and labelled unchanged."""
        session = FakeSession({"^GSPC": chart_payload([JAN2])})
        df = yh.Ticker("^gspc", session=session).history()
        assert df["ticker"].to_list() == ["^GSPC"]
        assert session.calls[0][0] == "^GSPC"


class TestTicker:
    """The single-symbol Ticker API."""

    def test_normalises_symbol(self):
        """Symbols are stripped and upper-cased; blank ones are rejected."""
        assert yh.Ticker(" aapl ").symbol == "AAPL"
        assert repr(yh.Ticker("aapl")) == "Ticker('AAPL')"
        with pytest.raises(ValueError, match="must not be empty"):
            yh.Ticker("  ")

    def test_history(self):
        """history() requests the given period and parses the bars."""
        session = FakeSession({"AAPL": chart_payload([JAN2, JAN3])})
        df = yh.Ticker("AAPL", session=session).history(period="5d")
        assert df.height == 2
        assert session.calls[0][1]["range"] == "5d"

    def test_dividends_and_splits_use_full_range(self):
        """Corporate actions are fetched over the full history."""
        events = {"dividends": {"1": {"amount": 0.5, "date": JAN2}}}
        session = FakeSession({"AAPL": chart_payload([JAN2], events=events)})
        ticker = yh.Ticker("AAPL", session=session)
        assert ticker.dividends()["dividend"].to_list() == [0.5]
        assert ticker.splits().is_empty()
        assert all(params["range"] == "max" for _, params in session.calls)

    def test_dividends_and_splits_share_one_request(self):
        """The full-history events payload is fetched once per Ticker."""
        session = FakeSession({"AAPL": chart_payload([JAN2])})
        ticker = yh.Ticker("AAPL", session=session)
        ticker.dividends()
        ticker.splits()
        assert len(session.calls) == 1


class TestDownload:
    """The multi-symbol download API."""

    def test_stacks_long_frame(self):
        """Symbols are de-duplicated and stacked, sorted by ticker."""
        session = FakeSession({"MSFT": chart_payload([JAN2, JAN3]), "AAPL": chart_payload([JAN2])})
        df = yh.download("msft, aapl msft", session=session)
        assert df["ticker"].to_list() == ["AAPL", "MSFT", "MSFT"]
        assert len(session.calls) == 2  # de-duplicated

    def test_threaded_by_default(self, monkeypatch):
        """Without a session, requests go through the per-thread default session."""
        session = FakeSession({"AAPL": chart_payload([JAN2]), "MSFT": chart_payload([JAN3])})
        monkeypatch.setattr(_http, "default_session", lambda: session)
        df = yh.download(["AAPL", "MSFT"], max_workers=4)
        assert sorted(df["ticker"].to_list()) == ["AAPL", "MSFT"]

    def test_failure_warns_and_skips(self):
        """A failing symbol is skipped with a warning."""
        session = FakeSession({"AAPL": chart_payload([JAN2]), "NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.warns(UserWarning, match="skipping NOPE"):
            df = yh.download(["AAPL", "NOPE"], session=session)
        assert df["ticker"].unique().to_list() == ["AAPL"]

    def test_network_failure_warns_and_skips(self):
        """A symbol whose request fails at the transport level is skipped, not fatal."""
        session = FakeSession({"AAPL": chart_payload([JAN2]), "MSFT": OSError("Connection reset")})
        with pytest.warns(UserWarning, match="skipping MSFT: MSFT: network error"):
            df = yh.download(["AAPL", "MSFT"], session=session)
        assert df["ticker"].unique().to_list() == ["AAPL"]

    def test_failure_raises_when_asked(self):
        """raise_errors=True propagates the failure."""
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.raises(yh.TickerNotFoundError):
            yh.download("NOPE", session=session, raise_errors=True)

    def test_all_failed_gives_empty_frame(self):
        """If every symbol fails the result is empty but keeps its columns."""
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.warns(UserWarning, match="skipping NOPE"):
            df = yh.download("NOPE", session=session)
        assert df.is_empty()
        assert df.columns[:2] == ["ticker", "date"]

    def test_mixed_timezones_intraday_become_utc(self):
        """Intraday frames on different time zones are unified in UTC."""
        session = FakeSession(
            {
                "AAPL": chart_payload([JAN2], adjclose=False),
                "SAP.DE": chart_payload([JAN2], tz="Europe/Berlin", adjclose=False),
            }
        )
        df = yh.download(["AAPL", "SAP.DE"], period="5d", interval="5m", session=session)
        assert df.schema["timestamp"] == pl.Datetime("us", "UTC")
        assert df["timestamp"].unique().to_list() == [dt.datetime(2024, 1, 2, 14, 30, tzinfo=dt.UTC)]

    def test_same_timezone_intraday_kept_local(self):
        """Intraday frames sharing a time zone keep it."""
        session = FakeSession({"AAPL": chart_payload([JAN2]), "MSFT": chart_payload([JAN3])})
        df = yh.download(["AAPL", "MSFT"], interval="1h", session=session)
        assert df.schema["timestamp"] == pl.Datetime("us", "America/New_York")

    def test_validates_before_requesting(self):
        """Bad arguments fail before any request is made."""
        session = FakeSession({})
        with pytest.raises(ValueError, match="use 15m"):
            yh.download("AAPL", interval="30m", session=session)
        with pytest.raises(ValueError, match="no tickers"):
            yh.download(" , ", session=session)
        assert session.calls == []


@pytest.mark.live
def test_live_round_trip():
    """Download real data from Yahoo (opt in with ``-m live``)."""
    df = yh.download(["AAPL", "MSFT", "^GSPC"], period="5d")  # ^GSPC is sent percent-encoded
    assert set(df["ticker"]) == {"AAPL", "MSFT", "^GSPC"}
    assert df["close"].null_count() == 0
    assert yh.Ticker("AAPL").splits().height >= 5
