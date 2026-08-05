from __future__ import annotations

from datetime import datetime
from datetime import timezone

from kairospy.application.usecases.reference.application.query import ReferenceQuery, ReferenceSelection
from kairospy.application.usecases.reference.protocol import ReferenceCatalogSource, ReferenceStore
from kairospy.application.usecases.reference.services.catalogs import ReferenceCatalogService
from kairospy.application.usecases.reference.services.operations import ReferenceRefreshWorkflow
from kairospy.application.usecases.reference.services.projections import ReferenceProjectionService
from kairospy.application.usecases.reference.services.refresh import ReferenceRefreshService
from kairospy.application.usecases.reference.services.resolution import ReferenceResolutionService
from kairospy.application.usecases.reference.services.universe import ReferenceUniverseService
from kairospy.domain.reference import LifecycleEvent, MarketDefinition, MarketRef, MarketResolver, OptionContractRef, ReferenceCatalog
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
        value: object | MarketRef,
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

    def market_definition(self, market_id: object, *, at: datetime) -> MarketDefinition | None:
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

    def ensure_ready(self):
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

    def add_asset(self, **kwargs):
        return self._catalogs.add_asset(**kwargs)

    def refresh_exchange(self, source, **kwargs):
        return self._refresh_workflow.exchange(source, **kwargs)

    def refresh_exchange_with_delist_schedule(self, source, **kwargs):
        return self._refresh_workflow.exchange_with_delist_schedule(source, **kwargs)

    def refresh_provider(self, source, **kwargs):
        return self._refresh_workflow.provider(source, **kwargs)

    def refresh_options(
        self,
        source: ReferenceCatalogSource | None = None,
        *,
        underlying: str = "SPY",
        as_of: datetime | None = None,
    ):
        """Refresh option contract identity into the Reference catalog."""
        selected = source or self._source
        if selected is None:
            raise RuntimeError("option reference refresh requires a catalog source")
        observed_at = as_of or datetime.now(timezone.utc)
        snapshot = selected.catalog(
            as_of=observed_at,
            market="option",
            params={"underlying": underlying},
        )
        result = self._refresh.refresh_snapshot(
            snapshot,
            as_of=observed_at,
            venue="massive",
            market="option",
        )
        self._ready = True
        return result

    def refresh_equity(self, source, **kwargs):
        return self._refresh_workflow.equity(source, **kwargs)

    def sync_lifecycle_events(self, source, **kwargs):
        return self._refresh_workflow.lifecycle_events(source, **kwargs)

    def register_views(self, views, *, as_of: datetime | None = None) -> None:
        ReferenceProjectionService(self.catalog(), self.lifecycle_events()).register_views(views, as_of=as_of)

    def publish_views(self, views, *, as_of: datetime | None = None) -> None:
        ReferenceProjectionService(self.catalog(), self.lifecycle_events()).publish_views(views, as_of=as_of)

    def bootstrap(self, *, as_of: datetime | None = None):
        if self._source is None:
            return None
        observed_at = as_of or datetime.now(timezone.utc)
        return self._refresh.refresh_snapshot(
            self._source.catalog(as_of=observed_at, market=self._default_market),
            as_of=observed_at,
            venue=self._default_venue,
            market=self._default_market,
        )

__all__ = ["ReferenceApplication", "ReferenceQuery", "ReferenceSelection"]
