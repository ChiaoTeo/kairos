from __future__ import annotations

from dataclasses import dataclass

import pytest

from kairospy.infrastructure.integrations.services.gateways.massive.client import MassiveStocksRequestError, MassiveStocksRestClient
from kairospy.domain.reference import SourceSymbol


@dataclass
class _Response:
    status_code: int = 200
    content: bytes = b"{}"
    text: str = "{}"

    def json(self):
        return {"results": []}


class _Driver:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, url, *, params=None, headers=None):
        self.calls.append((method, url, params, headers))
        return _Response()


def test_massive_aggregates_use_private_rest_endpoint() -> None:
    driver = _Driver()
    client = MassiveStocksRestClient(api_key="test-key", driver=driver)

    assert client.aggregates(
        "aapl", from_date="2025-11-03", to_date="2025-11-28"
    ) == {"results": []}
    assert driver.calls == [
        (
            "GET",
            "http://api.massiveprivateserver.site/v2/aggs/ticker/AAPL/range/1/day/2025-11-03/2025-11-28",
            {"adjusted": "true", "sort": "asc", "limit": 120, "apiKey": "test-key"},
            None,
        )
    ]


def test_massive_accepts_domain_source_symbol() -> None:
    driver = _Driver()
    client = MassiveStocksRestClient(api_key="test-key", driver=driver)

    client.aggregates(SourceSymbol("SPY"), from_date="2025-11-03", to_date="2025-11-28")

    assert "/ticker/SPY/" in driver.calls[0][1]


def test_massive_default_http_timeout_is_short_and_transport_reason_is_preserved() -> None:
    class FailingDriver:
        timeout_seconds = 10.0

        def request(self, method, url, *, params=None, headers=None):
            raise ConnectionResetError(54, "Connection reset by peer")

    client = MassiveStocksRestClient(api_key="test-key", driver=FailingDriver())

    with pytest.raises(MassiveStocksRequestError, match="ConnectionResetError.*Connection reset by peer"):
        client.aggregates("SPY", from_date="2026-01-01", to_date="2026-01-02")
