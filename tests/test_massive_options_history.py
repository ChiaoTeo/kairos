from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.infrastructure.integrations.services.gateways.massive.client import MassiveStocksRestClient
from kairospy.application.usecases.reference.application.builders import catalog_from_market_snapshot


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"results": [{"t": 1_700_000_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]}


class _Driver:
    def __init__(self) -> None:
        self.calls = []

    def request(self, method, url, *, params=None, headers=None):
        self.calls.append((method, url, params, headers))
        return _Response()


def test_massive_option_history_is_normalized_to_domain_bars() -> None:
    driver = _Driver()
    client = MassiveStocksRestClient(api_key="test-key", driver=driver)

    bars = list(client.bars(
        "O:SPY260821P00500000",
        timeframe="5m",
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        until="2026-01-02",
    ))

    assert bars[0].market_key == "massive_option_o_spy260821p00500000"
    assert bars[0].time.tzinfo is timezone.utc
    assert bars[0].close == Decimal("1.5")
    assert driver.calls[0][1].endswith("/range/5/minute/2026-01-01/2026-01-02")


def test_option_contracts_are_reference_rows_and_keep_contract_identity() -> None:
    driver = _Driver()
    client = MassiveStocksRestClient(api_key="test-key", driver=driver)
    rows = client.option_contracts("SPY", as_of="2026-01-01")

    # The fake response is intentionally reused; the client preserves vendor
    # rows and the application builder owns their domain translation.
    catalog = catalog_from_market_snapshot(
        [{
            "venue": "massive",
            "market": "option",
            "source_symbol": "O:SPY260821P00500000",
            "underlying_instrument_id": "instrument:equity:spy",
            "expiration_date": "2026-08-21",
            "strike_price": 500,
            "contract_type": "put",
            "shares_per_contract": 100,
            "active": True,
        }],
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    definition = catalog.list_markets(at=datetime(2026, 1, 1, tzinfo=timezone.utc), market="option")[0]
    instrument = catalog.get_instrument(definition.instrument_id, datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert len(tuple(rows)) == 1
    assert instrument.option_right == "put"
    assert instrument.strike == Decimal("500")
    assert instrument.multiplier == Decimal("100")
    assert instrument.expiry is not None
    assert driver.calls[0][1].endswith("/v3/reference/options/contracts")
