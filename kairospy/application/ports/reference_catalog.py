from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from kairospy.core.reference import ReferenceCatalog


class ReferenceCatalogSource(Protocol):
    """Application-facing reference source returning a domain catalog."""

    def fetch_catalog(
        self,
        *,
        as_of: datetime,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceCatalog:
        ...


__all__ = ["ReferenceCatalogSource"]
