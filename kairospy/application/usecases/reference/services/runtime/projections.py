from __future__ import annotations

from dataclasses import dataclass

from kairospy.domain.reference import LifecycleEvent, ReferenceCatalog


@dataclass(frozen=True, slots=True)
class RuntimeReferenceProjectionService:
    reference: object

    def catalog(self) -> ReferenceCatalog:
        return self.reference.catalog()

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.reference.lifecycle_events()


@dataclass(frozen=True, slots=True)
class RuntimeReferenceService:
    projection: RuntimeReferenceProjectionService

    def catalog(self) -> ReferenceCatalog:
        return self.projection.catalog()

    def lifecycle_events(self) -> tuple[LifecycleEvent, ...]:
        return self.projection.lifecycle_events()


__all__ = ["RuntimeReferenceProjectionService", "RuntimeReferenceService"]
