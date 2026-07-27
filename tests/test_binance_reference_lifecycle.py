from __future__ import annotations

from datetime import datetime, timezone

from kairospy.integrations import Binance, BinanceReferenceDriver, CcxtDriver
from kairospy.core.reference import LifecycleEventType


UTC = timezone.utc


class FakeMarketExchange:
    def load_markets(self, params=None):
        return {
            "OLD/USDT": {
                "id": "OLDUSDT",
                "symbol": "OLD/USDT",
                "base": "OLD",
                "quote": "USDT",
                "spot": True,
                "active": True,
            }
        }

    def close(self):
        pass


def test_binance_reference_driver_fetches_delist_schedule() -> None:
    requested = []

    def http_get(url, headers):
        requested.append((url, headers))
        return [{"delistTime": 1767225600000, "symbols": ["OLD/USDT", "OLDUSDT"]}]

    rows = list(BinanceReferenceDriver(api_key="key", http_get=http_get).fetch_delist_schedule())

    assert rows == [{
        "delist_time": "2026-01-01T00:00:00+00:00",
        "delist_time_ms": 1767225600000,
        "symbols": ("OLD/USDT", "OLDUSDT"),
        "raw": {"delistTime": 1767225600000, "symbols": ["OLD/USDT", "OLDUSDT"]},
    }]
    assert requested[0][0].endswith("/sapi/v1/spot/delist-schedule")
    assert requested[0][1] == {"X-MBX-APIKEY": "key"}


def test_binance_delist_schedule_maps_to_reference_events() -> None:
    def http_get(url, headers):
        return [{"delistTime": 1767225600000, "symbols": ["OLD/USDT"]}]

    binance = Binance(
        CcxtDriver(exchange_factory=lambda exchange_id: FakeMarketExchange()),
        BinanceReferenceDriver(http_get=http_get),
    )
    as_of = datetime(2025, 12, 31, tzinfo=UTC)
    catalog = binance.fetch_reference_catalog(as_of=as_of, params={"type": "spot"})

    events = binance.fetch_delist_events(catalog=catalog)

    assert len(events) == 1
    assert events[0].event_type is LifecycleEventType.DELISTED
    assert events[0].event_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert str(events[0].market_id) == "market:binance:spot:old_usdt"
    assert events[0].current == {"scheduled": True, "market": "spot"}
