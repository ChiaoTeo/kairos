from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from kairospy.domain.reference import Asset, AssetId, AssetType, EntityId, FinancialProductDefinition, LifecycleEvent, ReferenceCatalog
from kairospy.domain.reference.identity import reference_slug
from ..protocol import ReferenceStore



@dataclass(slots=True)
class ReferenceCatalogService:
    store: ReferenceStore
    _catalog: ReferenceCatalog | None = field(default=None, init=False, repr=False)
    _lifecycle_events: tuple[LifecycleEvent, ...] | None = field(default=None, init=False, repr=False)

    def catalog(self, *, reload: bool = False) -> ReferenceCatalog:
        if reload or self._catalog is None:
            self._catalog = self.store.load_catalog()
        return self._catalog

    def has_catalog(self) -> bool:
        marker = getattr(self.store, "has_catalog", None)
        if callable(marker):
            return bool(marker())
        catalog = self.catalog()
        return bool(catalog.entities() or catalog.markets() or catalog.financial_products())

    def save_catalog(self, catalog: ReferenceCatalog) -> ReferenceCatalog:
        self.store.save_catalog(catalog)
        self._catalog = catalog
        return catalog

    def lifecycle_events(self, *, reload: bool = False) -> tuple[LifecycleEvent, ...]:
        if reload or self._lifecycle_events is None:
            self._lifecycle_events = self.store.load_events()
        return self._lifecycle_events

    def append_events(self, events: tuple[LifecycleEvent, ...]) -> tuple[LifecycleEvent, ...]:
        self.store.append_events(events)
        current = self.lifecycle_events()
        self._lifecycle_events = current + tuple(events)
        return tuple(events)

    def add_asset(
        self,
        *,
        symbol: str,
        asset_type: AssetType | str,
        effective_from: datetime,
        asset_id: AssetId | str | None = None,
        name: str | None = None,
        issuer_id: EntityId | str | None = None,
        metadata: Mapping[str, object] | None = None,
        replace_existing: bool = False,
    ) -> Asset:
        catalog = self.catalog()
        resolved_asset_type = asset_type if isinstance(asset_type, AssetType) else AssetType(str(asset_type))
        resolved_asset_id = AssetId(str(asset_id)) if asset_id is not None else _asset_id(resolved_asset_type, symbol)
        asset = Asset(
            resolved_asset_id,
            resolved_asset_type,
            _required_text(symbol, "symbol"),
            name=_optional_text(name),
            issuer_id=None if issuer_id is None else EntityId(str(issuer_id)),
            effective_from=effective_from,
            metadata=metadata or {},
        )
        current = catalog.maybe_get_asset(resolved_asset_id, effective_from)
        if current is not None:
            if not replace_existing:
                raise ValueError(f"asset already exists at {effective_from.isoformat()}: {resolved_asset_id}")
            catalog.supersede_asset(asset, effective_from)
        else:
            catalog.add_asset(asset)
        self.save_catalog(catalog)
        return asset

    def add_financial_product(self, product: FinancialProductDefinition, *, replace_existing: bool = False) -> FinancialProductDefinition:
        catalog = self.catalog()
        current = catalog.maybe_get_financial_product(product.product_id, product.effective_from)
        if current is not None:
            if not replace_existing:
                raise ValueError(f"financial product already exists at {product.effective_from.isoformat()}: {product.product_id}")
            catalog.supersede_financial_product(product, product.effective_from)
        else:
            catalog.add_financial_product(product)
        self.save_catalog(catalog)
        return product

    def snapshot(self, *, as_of: datetime, reload: bool = False) -> ReferenceCatalog:
        catalog = self.catalog(reload=reload)
        return ReferenceCatalog(
            entities=catalog.entities(),
            assets=tuple(catalog.snapshot(at=as_of)["assets"].values()),
            instruments=tuple(catalog.snapshot(at=as_of)["instruments"].values()),
            listings=tuple(catalog.snapshot(at=as_of)["listings"].values()),
            markets=tuple(catalog.snapshot(at=as_of)["markets"].values()),
            financial_products=tuple(catalog.snapshot(at=as_of)["financial_products"].values()),
        )


__all__ = ["ReferenceCatalogService"]


def _asset_id(asset_type: AssetType, symbol: str) -> AssetId:
    return AssetId(f"asset:{asset_type.value}:{reference_slug(_required_text(symbol, 'symbol'))}")


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
