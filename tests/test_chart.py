"""Tests for the pure request-building and parsing layer."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from conftest import JAN2, JAN3, JAN4, JAN4_LIVE, JAN5, chart_payload

from polars_yahoo._chart import MAX_START, build_params, parse_dividends, parse_prices, parse_splits


def result(payload):
    """Unwrap the single ``result`` object from a chart payload."""
    return payload["chart"]["result"][0]


class TestBuildParams:
    """Argument validation and translation into chart query parameters."""

    def test_period(self):
        """A period becomes ``range``, with events requested."""
        p = build_params(period="1y", interval="1wk")
        assert p["range"] == "1y"
        assert p["interval"] == "1wk"
        assert p["events"] == "div,splits"
        assert "period1" not in p

    def test_start_end_take_precedence_and_are_utc(self):
        """start/end override period, and naive values are read as UTC."""
        p = build_params(period="1y", start=dt.date(2024, 1, 1), end="2024-01-02")
        assert (p["period1"], p["period2"]) == (1704067200, 1704153600)
        assert "range" not in p

    def test_aware_datetime_is_respected(self):
        """A tz-aware start is converted using its own offset."""
        start = dt.datetime(2024, 1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=1)))
        assert build_params(start=start, end="2024-01-02")["period1"] == 1704067200

    @pytest.mark.parametrize("interval", ["1d", "5d", "1wk"])
    def test_max_is_an_explicit_window(self, interval):
        """period='max' is sent as period1/period2: for range=max Yahoo returns monthly bars."""
        p = build_params(period="max", interval=interval)
        assert "range" not in p
        assert p["period1"] == MAX_START == int(dt.datetime(1900, 1, 1, tzinfo=dt.UTC).timestamp())
        assert p["period2"] > 0

    @pytest.mark.parametrize("interval", ["1mo", "3mo"])
    def test_max_stays_a_range_for_monthly_bars(self, interval):
        """Monthly and quarterly bars are what range=max returns anyway."""
        assert build_params(period="max", interval=interval)["range"] == "max"

    def test_start_without_end_runs_to_now(self):
        """A missing end defaults to the current time."""
        p = build_params(start="2024-01-01")
        assert p["period2"] > p["period1"]

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"interval": "30m"}, "15m"),
            ({"interval": "2h"}, "interval must be"),
            ({"period": "7d"}, "period must be"),
            ({"period": "max", "interval": "1h"}, "not available for intraday"),
            ({"end": "2024-01-01"}, "end requires start"),
            ({"start": "2024-01-02", "end": "2024-01-01"}, "after start"),
        ],
    )
    def test_rejects_bad_arguments(self, kwargs, match):
        """Invalid interval, period or range raises ValueError with a useful message."""
        with pytest.raises(ValueError, match=match):
            build_params(**kwargs)

    def test_prepost_flag(self):
        """Prepost is passed to Yahoo as a lowercase boolean string."""
        assert build_params(prepost=True)["includePrePost"] == "true"


class TestParsePrices:
    """Turning chart results into long price frames."""

    def test_daily_schema_and_values(self):
        """Daily bars have the documented schema and Yahoo's values."""
        df = parse_prices("AAPL", result(chart_payload([JAN2, JAN3])), "1d")
        assert df.schema == pl.Schema(
            {
                "ticker": pl.String,
                "date": pl.Date,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "adj_close": pl.Float64,
                "volume": pl.Int64,
            }
        )
        assert df["date"].to_list() == [dt.date(2024, 1, 2), dt.date(2024, 1, 3)]
        assert df["close"].to_list() == [100.0, 101.0]
        assert df["adj_close"].to_list() == pytest.approx([99.0, 99.99])
        assert df["volume"].to_list() == [1000, 2000]

    def test_drops_empty_rows_and_collapses_live_bar(self):
        """All-null rows are dropped and a duplicated live bar keeps the latest value."""
        payload = chart_payload([JAN2, JAN4, JAN4_LIVE, JAN5], close=[100.0, 102.0, 103.0, None])
        df = parse_prices("AAPL", result(payload), "1d")
        assert df["date"].to_list() == [dt.date(2024, 1, 2), dt.date(2024, 1, 4)]
        assert df["close"].to_list() == [100.0, 103.0]  # the live bar wins

    def test_date_is_exchange_local(self):
        """The date column uses the exchange's calendar, not UTC's."""
        # 2024-01-03 03:00 UTC is still 2024-01-02 in New York
        df = parse_prices("AAPL", result(chart_payload([1704250800])), "1d")
        assert df["date"].to_list() == [dt.date(2024, 1, 2)]

    def test_intraday_timestamp_is_tz_aware(self):
        """Intraday bars carry an exchange-tz timestamp and a null adj_close."""
        df = parse_prices("SAP.DE", result(chart_payload([JAN2], tz="Europe/Berlin", adjclose=False)), "5m")
        assert df.schema["timestamp"] == pl.Datetime("us", "Europe/Berlin")
        assert df["timestamp"][0] == dt.datetime(2024, 1, 2, 14, 30, tzinfo=dt.UTC)
        assert df["adj_close"].null_count() == 1

    def test_no_timestamps_gives_empty_frame_with_schema(self):
        """A result with no bars yields an empty frame that still has the schema."""
        payload = chart_payload([])
        del payload["chart"]["result"][0]["timestamp"]
        df = parse_prices("AAPL", result(payload), "1d")
        assert df.is_empty()
        assert "date" in df.columns

    def test_sorted_by_time(self):
        """Output is sorted by time whatever order Yahoo used."""
        df = parse_prices("AAPL", result(chart_payload([JAN3, JAN2])), "1d")
        assert df["date"].is_sorted()


EVENTS = {
    "dividends": {str(JAN3): {"amount": 0.24, "date": JAN3}, str(JAN2): {"amount": 0.23, "date": JAN2}},
    "splits": {str(JAN4): {"date": JAN4, "numerator": 4.0, "denominator": 1.0, "splitRatio": "4:1"}},
}


class TestParseEvents:
    """Dividend and split extraction from chart events."""

    def test_dividends(self):
        """Dividends become dated rows, sorted by date."""
        df = parse_dividends("AAPL", result(chart_payload([JAN2], events=EVENTS)))
        assert df.to_dicts() == [
            {"ticker": "AAPL", "date": dt.date(2024, 1, 2), "dividend": 0.23},
            {"ticker": "AAPL", "date": dt.date(2024, 1, 3), "dividend": 0.24},
        ]

    def test_splits(self):
        """Splits carry numerator, denominator and their ratio."""
        df = parse_splits("AAPL", result(chart_payload([JAN2], events=EVENTS)))
        assert df.to_dicts() == [
            {"ticker": "AAPL", "date": dt.date(2024, 1, 4), "numerator": 4.0, "denominator": 1.0, "ratio": 4.0}
        ]

    def test_no_events(self):
        """Missing events yield empty frames with the full column set."""
        r = result(chart_payload([JAN2]))
        assert parse_dividends("AAPL", r).is_empty()
        splits = parse_splits("AAPL", r)
        assert splits.is_empty()
        assert splits.columns == ["ticker", "date", "numerator", "denominator", "ratio"]
