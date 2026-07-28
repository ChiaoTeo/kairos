from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from kairospy.core.reference import LifecycleEventType, MarketStatus
from kairospy.service.domains.reference import ReferenceDataRefreshService, ReferenceRefreshService, ReferenceStore


UTC = timezone.utc


class FakeInstrumentProvider:
    def __init__(self, rows: Iterable[Mapping[str, object]]) -> None:
        self.rows = tuple(rows)

    def fetch_markets(self, *, params: Mapping[str, object] | None = None) -> Iterable[Mapping[str, object]]:
        assert params == {"type": "spot"}
        return self.rows


def test_reference_refresh_persists_provider_snapshot_and_lifecycle_events(tmp_path) -> None:
    store = ReferenceStore(tmp_path / "reference")
    service = ReferenceDataRefreshService(ReferenceRefreshService(store))
    first_time = datetime(2026, 1, 1, tzinfo=UTC)
    second_time = datetime(2026, 1, 2, tzinfo=UTC)

    first = service.refresh(
        FakeInstrumentProvider((
            _row("BTC/USDT", "BTC", active=True),
            _row("ETH/USDT", "ETH", active=True),
        )),
        as_of=first_time,
        venue="binance",
        market="spot",
        params={"type": "spot"},
    )

    assert [event.event_type for event in first.events] == [LifecycleEventType.LISTED, LifecycleEventType.LISTED]
    assert first.catalog.resolve_market("BTC/USDT", venue="binance", market="spot", at=first_time).status is MarketStatus.ACTIVE

    second = service.refresh(
        FakeInstrumentProvider((
            _row("BTC/USDT", "BTC", active=False),
            _row("SOL/USDT", "SOL", active=True),
        )),
        as_of=second_time,
        venue="binance",
        market="spot",
        params={"type": "spot"},
    )

    assert [event.event_type for event in second.events] == [
        LifecycleEventType.LISTED,
        LifecycleEventType.DELISTED,
        LifecycleEventType.STATUS_CHANGED,
    ]
    loaded = store.load_catalog()
    assert loaded.resolve_market("BTC/USDT", venue="binance", market="spot", at=first_time).status is MarketStatus.ACTIVE
    assert loaded.resolve_market("BTC/USDT", venue="binance", market="spot", at=second_time).status is MarketStatus.DELISTED
    assert loaded.resolve_market("ETH/USDT", venue="binance", market="spot", at=second_time).status is MarketStatus.DELISTED
    assert loaded.resolve_market("SOL/USDT", venue="binance", market="spot", at=second_time).status is MarketStatus.ACTIVE

    persisted_events = store.load_events()
    assert [event.event_type for event in persisted_events] == [
        LifecycleEventType.LISTED,
        LifecycleEventType.LISTED,
        LifecycleEventType.LISTED,
        LifecycleEventType.DELISTED,
        LifecycleEventType.STATUS_CHANGED,
    ]


def _row(symbol: str, base: str, *, active: bool) -> Mapping[str, object]:
    return {
        "venue": "binance",
        "market": "spot",
        "source_symbol": symbol,
        "venue_instrument_id": symbol.replace("/", ""),
        "base": base,
        "quote": "USDT",
        "active": active,
    }
