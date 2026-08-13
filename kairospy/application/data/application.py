"""Project-scoped Data use-case facade shared by external surfaces."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..workspace import Workspace
from .acquisition import DataAcquisitionApplication
from .catalog import DatasetCatalogApplication
from .gates import DataTrustGateApplication
from .models import (
    DataAcquisitionPlan,
    DataRequirement,
    DatasetDescription,
    DatasetRef,
    DatasetSetRef,
)
from .sets import DatasetSetRegistryApplication


class DataApplication:
    """Business-oriented Data operations used by CLI and client adapters."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    @property
    def _catalog(self) -> DatasetCatalogApplication:
        return DatasetCatalogApplication(self.workspace)

    def list(
        self,
        *,
        owner: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
    ) -> tuple[DatasetRef, ...]:
        return self._catalog.list(owner=owner, kind=kind, subject=subject)

    def describe(
        self, dataset_id: str, *, version: str | None = None
    ) -> DatasetDescription:
        return self._catalog.describe(dataset_id, version)

    async def plan(
        self, requirements: Iterable[DataRequirement]
    ) -> DataAcquisitionPlan:
        return self._catalog.plan(tuple(requirements))

    async def execute(
        self, plan: DataAcquisitionPlan, *, max_concurrency: int = 1
    ) -> DatasetSetRef:
        return await DataAcquisitionApplication(self.workspace, self._catalog).execute(
            plan, max_concurrency=max_concurrency
        )

    def execution(self, plan_hash: str) -> Mapping[str, Any]:
        return DataAcquisitionApplication(self.workspace, self._catalog).execution(
            plan_hash
        )

    def set_aliases(self) -> Mapping[str, str]:
        return DatasetSetRegistryApplication(self._catalog).aliases()

    def load_set(
        self, name: str, *, composition_hash: str | None = None
    ) -> DatasetSetRef:
        return DatasetSetRegistryApplication(self._catalog).load(
            name, composition_hash=composition_hash
        )

    def trust_report(self, composition_hash: str) -> Mapping[str, Any]:
        return DataTrustGateApplication(self._catalog).report(composition_hash)


__all__ = ["DataApplication"]
