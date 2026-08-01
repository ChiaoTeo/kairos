from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.components import RuntimeComponents
from kairospy.application.domain.reference import refresh_exchange_reference
from kairospy.infrastructure.persistence.reference.sqlite_store import ReferenceStore
from kairospy.application.runtime.services import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.runtime.services.reference import ReferenceCatalogService
from kairospy.core.intent import IntentJournal
from kairospy.core.reference import SourceSymbol
from kairospy.application.domain.reference.builders import catalog_from_market_rows


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


class FakeExchangeReference:
    def fetch_catalog(self, *, as_of: datetime, market: str | None = None, params=None):
        return catalog_from_market_rows(
            (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "status": "trading",
            },
            ),
            effective_from=as_of,
        )


def test_exchange_reference_refresh_feeds_runtime_view_state(tmp_path) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = ReferenceStore(tmp_path / "reference")

    result = refresh_exchange_reference(
        store,
        FakeExchangeReference(),
        as_of=as_of,
        venue="binance",
        market="spot",
        params={"type": "spot"},
    )
    service = ReferenceCatalogService(store, default_venue="binance", default_market="spot")
    kernel = RuntimeKernel(
        NoopStrategy(),
        components=RuntimeComponents(reference=service),
        services=RuntimeApplicationServices.from_dependencies(
            RuntimeServiceDependencies(intents=IntentJournal(), reference=service)
        ),
    )

    kernel.start()

    assert len(result.refresh.current_markets) == 1
    assert len(store.load_events()) == 1
    resolved = service.resolver(as_of=as_of).resolve("BTC/USDT")
    assert isinstance(resolved.source_symbol, SourceSymbol)
    assert str(resolved.source_symbol) == "BTC/USDT"
    assert kernel.views.require("reference.catalog").market_count == 1
    assert kernel.views.require("reference.markets").markets[0].market_key == "binance_spot_btc_usdt"
    assert kernel.context.reference.resolve("BTC/USDT", venue="binance", market="spot") == resolved
    assert kernel.views.require("reference.catalog").lifecycle_event_count == 1
