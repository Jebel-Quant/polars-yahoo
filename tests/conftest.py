"""Fake HTTP layer and synthetic chart payloads — no test here touches the network."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

# 2024-01-02 .. 2024-01-05, 09:30 New York (14:30 UTC)
JAN2, JAN3, JAN4, JAN5 = 1704205800, 1704292200, 1704378600, 1704465000
JAN4_LIVE = 1704398400  # 15:00 New York, same calendar day as JAN4


def chart_payload(
    timestamps: list[int],
    *,
    tz: str = "America/New_York",
    close: list[float | None] | None = None,
    adjclose: bool = True,
    events: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a chart response shaped like Yahoo's."""
    n = len(timestamps)
    close = close if close is not None else [100.0 + i for i in range(n)]
    quote = {
        "open": [None if c is None else c - 1 for c in close],
        "high": [None if c is None else c + 1 for c in close],
        "low": [None if c is None else c - 2 for c in close],
        "close": close,
        "volume": [None if c is None else 1000 * (i + 1) for i, c in enumerate(close)],
    }
    indicators: dict[str, Any] = {"quote": [quote]}
    if adjclose:
        indicators["adjclose"] = [{"adjclose": [None if c is None else c * 0.99 for c in close]}]
    result: dict[str, Any] = {
        "meta": {"exchangeTimezoneName": tz},
        "timestamp": timestamps,
        "indicators": indicators,
    }
    if events:
        result["events"] = events
    return {"chart": {"result": [result], "error": None}}


NOT_FOUND = {
    "chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}
}


@dataclass
class FakeResponse:
    status_code: int
    body: Any

    def json(self) -> Any:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@dataclass
class FakeSession:
    """Answers by ticker; a list of responses is consumed one per call."""

    routes: dict[str, Any]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get(self, url: str, *, params: dict[str, Any], timeout: float) -> FakeResponse:
        ticker = url.rsplit("/", 1)[-1]
        self.calls.append((ticker, params))
        route = self.routes[ticker]
        if isinstance(route, list):
            route = route.pop(0)
        if isinstance(route, FakeResponse):
            return route
        return FakeResponse(200, route)


@pytest.fixture
def fake_session():
    return FakeSession
