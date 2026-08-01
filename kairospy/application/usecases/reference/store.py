from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from kairospy.core.reference import LifecycleEvent, ReferenceCatalog


class ReferenceStore(Protocol):
    def save_catalog(self, catalog: ReferenceCatalog) -> None:
        ...

    def load_catalog(self) -> ReferenceCatalog:
        ...

    def append_events(self, events: Iterable[LifecycleEvent]) -> object:
        ...

    def load_events(self) -> tuple[LifecycleEvent, ...]:
        ...


__all__ = ["ReferenceStore"]
