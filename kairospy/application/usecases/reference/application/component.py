from __future__ import annotations

from datetime import datetime
from datetime import timezone

from kairospy.application.usecases.reference.application.query import ReferenceQuery, ReferenceSelection
from kairospy.application.usecases.reference.application.requests import (
    ReferenceCatalogRequest,
    ReferenceLifecycleSyncRequest,
    ReferenceRefreshRequest,
)
from kairospy.application.usecases.reference.application.results import ReferenceProviderRefreshResult, ReferenceRefreshResult
from kairospy.application.usecases.reference.protocol import (
    ReferenceCatalogDelistSource,
    ReferenceCatalogSource,
    ReferenceLifecycleSource,
    ReferenceStore,
)
from kairospy.application.usecases.reference.services.catalogs import ReferenceCatalogService
from kairospy.application.usecases.reference.services.operations import ReferenceRefreshWorkflow
from kairospy.application.usecases.reference.services.projections import ReferenceProjectionService
from kairospy.application.usecases.reference.services.refresh import ReferenceRefreshService
from kairospy.application.usecases.reference.services.resolution import ReferenceResolutionService
from kairospy.application.usecases.reference.services.universe import ReferenceUniverseService
from kairospy.domain.reference import Asset, AssetId, AssetType, EntityId, FinancialProductDefinition, FinancialProductType, LifecycleEvent, MarketDefinition, MarketId, MarketRef, MarketResolver, OptionContractRef, ReferenceCatalog, SymbolRef
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.domain.market.selection import MarketSelectionQuery


class ReferenceApplication:
    """System-scoped public reference component backed by private services."""

    def __init__(
        self,
        store: ReferenceStore,
        *,
        default_venue: str | None = None,
        default_market: str | None = None,
        source: ReferenceCatalogSource | None = None,
    ) -> None:
        self._catalogs = ReferenceCatalogService(store)
        self._refresh = ReferenceRefreshService(self._catalogs)
        self._refresh_workflow = ReferenceRefreshWorkflow(store)
        self._store = store
        self._default_venue = default_venue
        self._default_market = default_market
        self._source = source
        self._ready = False

    def catalog(self, *, reload: bool = False) -> ReferenceCatalog:
        return self._catalogs.catalog(reload=reload)

    def save_catalog(self, catalog: ReferenceCatalog) -> ReferenceCatalog:
        return self._catalogs.save_catalog(catalog)

    def lifecycle_events(self, *, reload: bool = False) -> tuple[LifecycleEvent, ...]:
        return self._catalogs.lifecycle_events(reload=reload)

    def has_catalog(self) -> bool:
        return self._catalogs.has_catalog()

    def has_source(self) -> bool:
        return self._source is not None

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        return ReferenceResolutionService(
            self.catalog(), default_venue=self._default_venue, default_market=self._default_market
        ).resolver(as_of=as_of)

    def resolve(
        self,
        value: SymbolRef | MarketRef | str,
        *,
        venue: str | None = None,
        market: str | None = None,
        as_of: datetime | None = None,
    ) -> MarketRef:
        if not self._ready:
            self.ensure_ready()
        return ReferenceResolutionService(
            self.catalog(), default_venue=self._default_venue, default_market=self._default_market
        ).resolve(value, venue=venue, market=market, as_of=as_of)

    def market_definition(self, market_id: MarketId | str, *, at: datetime) -> MarketDefinition | None:
        """Return the effective market contract used by execution rule checks."""
        if not self._ready:
            self.ensure_ready()
        return self.catalog().maybe_get_market(market_id, at)

    def query(self, request: ReferenceQuery) -> ReferenceSelection:
        """Select resolved markets for a strategy subscription."""
        if not self._ready:
            self.ensure_ready()
        return ReferenceUniverseService(self.catalog()).select_markets(request)

    def option_contracts(self, request: MarketSelectionQuery | None = None) -> tuple[OptionContractRef, ...]:
        """Return stable option identities; quotes remain owned by Market."""
        if not self._ready:
            self.ensure_ready()
        query = request or MarketSelectionQuery(market="option", instrument_type="option")
        selection = ReferenceUniverseService(self.catalog()).select_markets(query)
        contracts: list[OptionContractRef] = []
        for market in selection.markets:
            definition = self.catalog().get_instrument(market.instrument_id, selection.as_of)
            if definition.expiry is None or definition.strike is None or definition.option_right is None:
                continue
            contracts.append(OptionContractRef(
                market=market,
                underlying_instrument_id=definition.underlying_instrument_id or "instrument:equity:spy",
                expiry=definition.expiry,
                strike=definition.strike,
                right=definition.option_right,
                multiplier=definition.multiplier or 100,
            ))
        return tuple(contracts)

    def ensure_ready(self) -> ReferenceCatalog:
        """Ensure a usable catalog exists before a strategy is started.

        A concrete store may expose ``has_catalog`` to distinguish an absent
        snapshot from a valid empty catalog.  Test stores and other ports can
        omit it; in that case a source plus an empty catalog is treated as a
        missing snapshot.
        """
        if self._ready:
            return self.catalog()
        catalog = self.catalog()
        stored = self._catalogs.has_catalog()
        if not stored:
            if self._source is None:
                raise RuntimeError("reference catalog is not initialized and no source is configured")
            result = self.bootstrap()
            if result is None:
                raise RuntimeError("reference catalog bootstrap did not produce a snapshot")
            catalog = self.catalog(reload=True)
        self._ready = True
        return catalog

    def add_asset(
        self,
        *,
        symbol: str,
        asset_type: AssetType | str,
        effective_from: datetime,
        asset_id: AssetId | str | None = None,
        name: str | None = None,
        issuer_id: EntityId | str | None = None,
        metadata: dict[str, object] | None = None,
        replace_existing: bool = False,
    ) -> Asset:
        return self._catalogs.add_asset(
            symbol=symbol,
            asset_type=asset_type,
            effective_from=effective_from,
            asset_id=asset_id,
            name=name,
            issuer_id=issuer_id,
            metadata=metadata,
            replace_existing=replace_existing,
        )

    def add_financial_product(self, product: FinancialProductDefinition, *, replace_existing: bool = False) -> FinancialProductDefinition:
        return self._catalogs.add_financial_product(product, replace_existing=replace_existing)

    def financial_products(
        self,
        *,
        at: datetime,
        product_type: FinancialProductType | str | None = None,
        asset_id: str | None = None,
    ) -> tuple[FinancialProductDefinition, ...]:
        return self.catalog().list_financial_products(at=at, product_type=product_type, asset_id=asset_id)

    def refresh_exchange(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceProviderRefreshResult:
        return self._refresh_workflow.exchange(source, request)

    def refresh_exchange_with_delist_schedule(self, source: ReferenceCatalogDelistSource, request: ReferenceRefreshRequest) -> ReferenceProviderRefreshResult:
        return self._refresh_workflow.exchange_with_delist_schedule(source, request)

    def refresh_provider(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceProviderRefreshResult:
        return self._refresh_workflow.provider(source, request)

    def refresh_options(
        self,
        source: ReferenceCatalogSource | None = None,
        *,
        underlying: str = "SPY",
        as_of: datetime | None = None,
    ) -> ReferenceRefreshResult:
        """Refresh option contract identity into the Reference catalog."""
        selected = source or self._source
        if selected is None:
            raise RuntimeError("option reference refresh requires a catalog source")
        observed_at = as_of or datetime.now(timezone.utc)
        snapshot = selected.catalog(
            ReferenceCatalogRequest(as_of=observed_at, market="option", underlying=underlying)
        )
        result = self._refresh.refresh_snapshot(
            snapshot,
            as_of=observed_at,
            venue="massive",
            market="option",
        )
        self._ready = True
        return result

    def refresh_equity(self, source: ReferenceCatalogSource, request: ReferenceRefreshRequest) -> ReferenceProviderRefreshResult:
        return self._refresh_workflow.equity(source, request)

    def sync_lifecycle_events(self, source: ReferenceLifecycleSource, request: ReferenceLifecycleSyncRequest) -> tuple[LifecycleEvent, ...]:
        return self._refresh_workflow.lifecycle_events(source, request)

    def register_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        ReferenceProjectionService(self.catalog(), self.lifecycle_events()).register_views(views, as_of=as_of)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        ReferenceProjectionService(self.catalog(), self.lifecycle_events()).publish_views(views, as_of=as_of)

    def bootstrap(self, *, as_of: datetime | None = None) -> ReferenceRefreshResult | None:
        if self._source is None:
            return None
        observed_at = as_of or datetime.now(timezone.utc)
        return self._refresh.refresh_snapshot(
            self._source.catalog(ReferenceCatalogRequest(as_of=observed_at, market=self._default_market)),
            as_of=observed_at,
            venue=self._default_venue,
            market=self._default_market,
        )

__all__ = ["ReferenceApplication", "ReferenceQuery", "ReferenceSelection"]
