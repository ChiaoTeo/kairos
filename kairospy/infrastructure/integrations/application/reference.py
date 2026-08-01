from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from kairospy.core.reference import ReferenceCatalog
from kairospy.infrastructure.integrations.adapters.reference_catalog import ReferenceCatalogAdapter
from kairospy.infrastructure.integrations.domain.bindings import ReferenceSourceRef
from kairospy.infrastructure.integrations.services.resolver import DEFAULT_INTEGRATION_RESOLVER, IntegrationResolver


@dataclass(frozen=True, slots=True)
class ReferenceIntegrationApplicationService:
    """Concrete reference integration service exposed to application composition."""

    source: ReferenceSourceRef
    resolver: IntegrationResolver = DEFAULT_INTEGRATION_RESOLVER
    default_market: str | None = None
    error_type: type[Exception] = ValueError

    @classmethod
    def provider(cls, provider_name: str, *, default_market: str | None = None, error_type: type[Exception] = ValueError) -> "ReferenceIntegrationApplicationService":
        return cls(ReferenceSourceRef("provider", provider_name), default_market=default_market, error_type=error_type)

    @classmethod
    def source_ref(
        cls,
        source_kind: str,
        source_name: str,
        *,
        market: str | None = None,
        error_type: type[Exception] = ValueError,
    ) -> "ReferenceIntegrationApplicationService":
        return cls(ReferenceSourceRef(source_kind, source_name, market=market), default_market=market, error_type=error_type)

    def fetch_catalog(
        self,
        *,
        as_of: datetime,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceCatalog:
        source = self.resolver.reference_data(self.source, error_type=self.error_type)
        return ReferenceCatalogAdapter(source, default_market=self.default_market).fetch_catalog(as_of=as_of, market=market, params=params)


__all__ = ["ReferenceIntegrationApplicationService"]
