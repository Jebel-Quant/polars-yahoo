from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from conftest import JAN2, JAN3, NOT_FOUND, FakeResponse, FakeSession, chart_payload

import polars_yahoo as yh
from polars_yahoo import _http


class TestHttp:
    def test_retries_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(_http.time, "sleep", lambda s: None)
        session = FakeSession({"AAPL": [FakeResponse(429, {}), chart_payload([JAN2])]})
        assert _http.get_chart("AAPL", {}, session=session)["timestamp"] == [JAN2]
        assert len(session.calls) == 2

    def test_gives_up_after_retries(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(_http.time, "sleep", sleeps.append)
        session = FakeSession({"AAPL": [FakeResponse(429, {})] * 3})
        with pytest.raises(yh.RateLimitError):
            _http.get_chart("AAPL", {}, session=session, retries=2, backoff=0.5)
        assert sleeps == [0.5, 1.0]

    def test_not_found(self):
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.raises(yh.TickerNotFoundError, match="delisted") as info:
            _http.get_chart("NOPE", {}, session=session)
        assert info.value.ticker == "NOPE"

    def test_non_json_body(self):
        session = FakeSession({"AAPL": FakeResponse(502, ValueError("not json"))})
        with pytest.raises(yh.YahooError, match="HTTP 502"):
            _http.get_chart("AAPL", {}, session=session)

    def test_server_error_with_json_error(self):
        session = FakeSession(
            {"AAPL": FakeResponse(500, {"chart": {"result": None, "error": {"description": "boom"}}})}
        )
        with pytest.raises(yh.YahooError, match="boom") as info:
            _http.get_chart("AAPL", {}, session=session)
        assert not isinstance(info.value, yh.TickerNotFoundError)

    def test_default_session_is_per_thread(self):
        import threading

        seen = []
        t = threading.Thread(target=lambda: seen.append(_http.default_session()))
        t.start()
        t.join()
        assert _http.default_session() is _http.default_session()
        assert seen[0] is not _http.default_session()


class TestTicker:
    def test_normalises_symbol(self):
        assert yh.Ticker(" aapl ").symbol == "AAPL"
        assert repr(yh.Ticker("aapl")) == "Ticker('AAPL')"
        with pytest.raises(ValueError):
            yh.Ticker("  ")

    def test_history(self):
        session = FakeSession({"AAPL": chart_payload([JAN2, JAN3])})
        df = yh.Ticker("AAPL", session=session).history(period="5d")
        assert df.height == 2
        assert session.calls[0][1]["range"] == "5d"

    def test_dividends_and_splits_use_full_range(self):
        events = {"dividends": {"1": {"amount": 0.5, "date": JAN2}}}
        session = FakeSession({"AAPL": chart_payload([JAN2], events=events)})
        ticker = yh.Ticker("AAPL", session=session)
        assert ticker.dividends()["dividend"].to_list() == [0.5]
        assert ticker.splits().is_empty()
        assert all(params["range"] == "max" for _, params in session.calls)


class TestDownload:
    def test_stacks_long_frame(self):
        session = FakeSession({"MSFT": chart_payload([JAN2, JAN3]), "AAPL": chart_payload([JAN2])})
        df = yh.download("msft, aapl msft", session=session)
        assert df["ticker"].to_list() == ["AAPL", "MSFT", "MSFT"]
        assert len(session.calls) == 2  # de-duplicated

    def test_threaded_by_default(self, monkeypatch):
        session = FakeSession({"AAPL": chart_payload([JAN2]), "MSFT": chart_payload([JAN3])})
        monkeypatch.setattr(_http, "default_session", lambda: session)
        df = yh.download(["AAPL", "MSFT"], max_workers=4)
        assert sorted(df["ticker"].to_list()) == ["AAPL", "MSFT"]

    def test_failure_warns_and_skips(self):
        session = FakeSession({"AAPL": chart_payload([JAN2]), "NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.warns(UserWarning, match="skipping NOPE"):
            df = yh.download(["AAPL", "NOPE"], session=session)
        assert df["ticker"].unique().to_list() == ["AAPL"]

    def test_failure_raises_when_asked(self):
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.raises(yh.TickerNotFoundError):
            yh.download("NOPE", session=session, raise_errors=True)

    def test_all_failed_gives_empty_frame(self):
        session = FakeSession({"NOPE": FakeResponse(404, NOT_FOUND)})
        with pytest.warns(UserWarning):
            df = yh.download("NOPE", session=session)
        assert df.is_empty()
        assert df.columns[:2] == ["ticker", "date"]

    def test_mixed_timezones_intraday_become_utc(self):
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
        session = FakeSession({"AAPL": chart_payload([JAN2]), "MSFT": chart_payload([JAN3])})
        df = yh.download(["AAPL", "MSFT"], interval="1h", session=session)
        assert df.schema["timestamp"] == pl.Datetime("us", "America/New_York")

    def test_validates_before_requesting(self):
        session = FakeSession({})
        with pytest.raises(ValueError):
            yh.download("AAPL", interval="30m", session=session)
        with pytest.raises(ValueError, match="no tickers"):
            yh.download(" , ", session=session)
        assert session.calls == []


@pytest.mark.live
def test_live_round_trip():
    df = yh.download(["AAPL", "MSFT"], period="5d")
    assert set(df["ticker"]) == {"AAPL", "MSFT"}
    assert df["close"].null_count() == 0
    assert yh.Ticker("AAPL").splits().height >= 5
