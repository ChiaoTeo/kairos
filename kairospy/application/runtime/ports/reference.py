from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.reference import LifecycleEvent, MarketResolver, ReferenceCatalog


class ReferencePort(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...

    def catalog(self) -> ReferenceCatalog:
        ...

    def resolver(self, *, as_of: datetime | None = None) -> MarketResolver:
        ...

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        ...


__all__ = ["ReferencePort"]
