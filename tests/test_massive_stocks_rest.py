from __future__ import annotations

from dataclasses import dataclass

from kairospy.infrastructure.integrations.services.gateways.massive.client import MassiveStocksRestClient


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
