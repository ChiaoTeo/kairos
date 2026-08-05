"""Ports consumed by the reference usecase.

Implementations are selected by composition.  Reference application code does
not depend on SQLite, vendor clients, or integration services.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from kairospy.domain.reference import LifecycleEvent, ReferenceCatalog
from kairospy.application.usecases.reference.application.requests import (
    ReferenceCatalogRequest,
    ReferenceDelistRequest,
    ReferenceLifecycleRequest,
)


class ReferenceStore(Protocol):
    def save_catalog(self, catalog: ReferenceCatalog) -> None: ...

    def load_catalog(self) -> ReferenceCatalog: ...

    def append_events(self, events: Iterable[LifecycleEvent]) -> Path: ...

    def load_events(self) -> tuple[LifecycleEvent, ...]: ...


class ReferenceCatalogSource(Protocol):
    def catalog(self, request: ReferenceCatalogRequest) -> ReferenceCatalog: ...


class ReferenceDelistScheduleSource(Protocol):
    def fetch_delist_events(self, request: ReferenceDelistRequest) -> Iterable[LifecycleEvent]: ...


class ReferenceLifecycleSource(Protocol):
    def fetch_lifecycle_events(self, request: ReferenceLifecycleRequest) -> Iterable[LifecycleEvent]: ...


class ReferenceCatalogDelistSource(ReferenceCatalogSource, ReferenceDelistScheduleSource, Protocol):
    """Catalog source that can also provide scheduled delist facts."""


class ReferenceProviderSource(ReferenceCatalogSource, ReferenceDelistScheduleSource, ReferenceLifecycleSource, Protocol):
    """Provider capability used by catalog refresh and lifecycle queries."""


__all__ = [
    "ReferenceCatalogRequest",
    "ReferenceCatalogDelistSource",
    "ReferenceCatalogSource",
    "ReferenceDelistRequest",
    "ReferenceDelistScheduleSource",
    "ReferenceLifecycleRequest",
    "ReferenceLifecycleSource",
    "ReferenceProviderSource",
    "ReferenceStore",
]
