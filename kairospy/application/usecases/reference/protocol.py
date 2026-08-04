"""Ports consumed by the reference usecase.

Implementations are selected by composition.  Reference application code does
not depend on SQLite, vendor clients, or integration services.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

from kairospy.domain.reference import LifecycleEvent, ReferenceCatalog


class ReferenceStore(Protocol):
    def save_catalog(self, catalog: ReferenceCatalog) -> None: ...

    def load_catalog(self) -> ReferenceCatalog: ...

    def append_events(self, events: Iterable[LifecycleEvent]) -> Path: ...

    def load_events(self) -> tuple[LifecycleEvent, ...]: ...


class ReferenceCatalogSource(Protocol):
    def catalog(
        self,
        *,
        as_of: datetime,
        market: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> ReferenceCatalog: ...


class ReferenceLifecycleSource(Protocol):
    def fetch_lifecycle_events(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        catalog: ReferenceCatalog,
        venue: str | None = None,
    ) -> Iterable[LifecycleEvent]: ...


__all__ = ["ReferenceCatalogSource", "ReferenceLifecycleSource", "ReferenceStore"]
