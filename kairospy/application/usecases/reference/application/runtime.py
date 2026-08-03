"""Runtime-facing assembly entry points for the reference usecase."""

from __future__ import annotations

from kairospy.application.usecases.reference.services.runtime import ReferenceCatalogService
from kairospy.application.usecases.reference.services.runtime.projections import (
    RuntimeReferenceProjectionService,
    RuntimeReferenceService,
)

__all__ = [
    "ReferenceCatalogService",
    "RuntimeReferenceProjectionService",
    "RuntimeReferenceService",
]
