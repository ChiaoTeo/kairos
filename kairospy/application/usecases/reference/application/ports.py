"""Reference application resource ports."""

from __future__ import annotations

from typing import Protocol

from kairospy.application.usecases.reference.protocol import (
    ReferenceCatalogSource,
    ReferenceProviderSource,
    ReferenceStore,
)
from kairospy.application.usecases.reference.application.requests import ReferenceDriverName, ReferenceExchangeName


class ReferenceCommandResources(Protocol):
    def reference_store(self, root: str | None) -> ReferenceStore: ...
    def public_market_access(self, exchange_name: ReferenceExchangeName, driver_name: ReferenceDriverName) -> ReferenceCatalogSource: ...
    def provider(self, provider_name: str, driver_name: ReferenceDriverName) -> ReferenceProviderSource: ...
    def reference_access(self, source_kind: str, source_name: str, *, market: str | None, driver_name: ReferenceDriverName) -> ReferenceCatalogSource: ...


__all__ = ["ReferenceCommandResources"]
