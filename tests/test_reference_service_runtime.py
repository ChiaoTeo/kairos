from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.service.domain.reference import ReferenceStore, refresh_instrument_provider
from kairospy.application.service.runtime.reference import ReferenceCatalogService


class NoopStrategy:
    strategy_id = "reference-strategy"

    def on_start(self, context: object) -> None:
        return None

    def on_data(self, context: object, signal: object) -> None:
        return None

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        return None


class FakeReferenceProvider:
    def fetch_markets(self, *, params: Mapping[str, object] | None = None) -> Iterable[Mapping[str, object]]:
        return (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "status": "trading",
            },
        )


def test_reference_provider_refresh_feeds_runtime_view_state(tmp_path) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = ReferenceStore(tmp_path / "reference")

    result = refresh_instrument_provider(
        store,
        FakeReferenceProvider(),
        as_of=as_of,
        venue="binance",
        market="spot",
        params={"type": "spot"},
    )
    service = ReferenceCatalogService(store, default_venue="binance", default_market="spot")
    kernel = RuntimeKernel(NoopStrategy(), reference=service)

    kernel.start()

    assert len(result.refresh.current_markets) == 1
    assert len(store.load_events()) == 1
    resolved = service.resolver(as_of=as_of).resolve("BTC/USDT")
    assert resolved.source_symbol == "BTC/USDT"
    assert kernel.views.require("reference.catalog").market_count == 1
    assert kernel.views.require("reference.catalog").lifecycle_event_count == 1
