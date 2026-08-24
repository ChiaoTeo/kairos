"""Research- and strategy-facing dataset convenience API.

This module is a surface adapter over the application data boundary.  It does
not own a catalog or duplicate reader semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from kairospy.research.apps.data.application import (
    DataAcquisitionPlan,
    DataAcquisitionApplication,
    DataApplication,
    DataRequirement,
    DataTrustGateApplication,
    DataTrustGateReport,
    DataUnavailableError,
    DatasetAnalyticalView,
    DatasetCatalogApplication,
    DatasetDescription,
    DatasetReadPlan,
    DatasetReaderApplication,
    DatasetRef,
    DatasetSetRef,
    DatasetSetRegistryApplication,
    DatasetSnapshot,
    OptionMarketDataTarget,
    OptionMarketPreparationApplication,
    ReplayStream,
)
from kairospy.system.apps.workspace.application import Workspace


@dataclass(frozen=True, slots=True)
class DataCatalogClient:
    """Public catalog capability without exposing its Application instance."""

    workspace: Workspace

    @property
    def _application(self) -> DatasetCatalogApplication:
        return DatasetCatalogApplication(self.workspace)

    def list(
        self,
        *,
        owner: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
    ) -> tuple[DatasetRef, ...]:
        return self._application.list(owner=owner, kind=kind, subject=subject)

    def inspect(self, dataset_id: str, version: str | None = None) -> DatasetRef:
        return self._application.inspect(dataset_id, version)

    def describe(
        self, dataset_id: str, version: str | None = None
    ) -> DatasetDescription:
        return self._application.describe(dataset_id, version)

    def publish(self, **request: Any) -> DatasetRef:
        return self._application.publish(**request)

    def publish_derived(self, **request: Any) -> DatasetRef:
        return self._application.publish_derived(**request)


@dataclass(frozen=True, slots=True)
class DataClient:
    """Typed convenience entry to the Workspace-scoped data applications."""

    workspace: Workspace

    @property
    def _application(self) -> DataApplication:
        return DataApplication(self.workspace)

    @property
    def catalog(self) -> DataCatalogClient:
        return DataCatalogClient(self.workspace)

    @property
    def _catalog(self) -> DatasetCatalogApplication:
        return DatasetCatalogApplication(self.workspace)

    @property
    def _readers(self) -> DatasetReaderApplication:
        return DatasetReaderApplication(self._catalog)

    def list(
        self,
        *,
        owner: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
    ) -> tuple[DatasetRef, ...]:
        """List logical Dataset references without exposing physical storage."""

        return self._application.list(owner=owner, kind=kind, subject=subject)

    def inspect(self, dataset_id: str, *, version: str | None = None) -> DatasetRef:
        """Resolve one immutable logical Dataset identity."""

        return self._catalog.inspect(dataset_id, version)

    def describe(
        self, dataset_id: str, *, version: str | None = None
    ) -> DatasetDescription:
        """Inspect lineage, quality and partition summaries without storage details."""

        return self._application.describe(dataset_id, version=version)

    def pin_set(
        self,
        name: str,
        dataset_set: DatasetSetRef,
        *,
        expected_current_hash: str | None = None,
    ) -> DatasetSetRef:
        """Persist one fixed composition and explicitly move its Project alias."""

        return DatasetSetRegistryApplication(self._catalog).pin(
            name,
            dataset_set,
            expected_current_hash=expected_current_hash,
        )

    def load_set(
        self, name: str, *, composition_hash: str | None = None
    ) -> DatasetSetRef:
        """Load the current or an explicitly versioned named Dataset Set."""

        return self._application.load_set(name, composition_hash=composition_hash)

    def set_aliases(self) -> Mapping[str, str]:
        return self._application.set_aliases()

    async def resolve(
        self,
        requirements: Iterable[DataRequirement],
        *,
        composition_policy: Mapping[str, Any] | None = None,
    ) -> DatasetSetRef:
        dataset_set, missing = self._catalog.resolve(
            tuple(requirements), composition_policy=composition_policy
        )
        if dataset_set is None:
            raise DataUnavailableError(missing)
        return dataset_set

    async def plan(
        self, requirements: Iterable[DataRequirement]
    ) -> DataAcquisitionPlan:
        return await self._application.plan(tuple(requirements))

    def option_market_requirements(
        self,
        targets: Iterable[OptionMarketDataTarget],
        *,
        kinds: tuple[str, ...] = ("quote",),
        credential_id: str = "massive-readonly",
        endpoint: str | None = None,
    ) -> tuple[DataRequirement, ...]:
        """Prepare resumable per-contract requirements for Massive Market data."""

        return OptionMarketPreparationApplication().requirements(
            targets,
            kinds=kinds,
            credential_id=credential_id,
            endpoint=endpoint,
        )

    async def execute(
        self, plan: DataAcquisitionPlan, *, max_concurrency: int = 1
    ) -> DatasetSetRef:
        return await self._application.execute(plan, max_concurrency=max_concurrency)

    def execution(self, plan_hash: str) -> Mapping[str, Any]:
        """Inspect durable progress for an explicit acquisition execution."""

        return self._application.execution(plan_hash)

    async def ensure(
        self,
        requirements: Iterable[DataRequirement],
        *,
        acquire_missing: bool = False,
        max_concurrency: int = 1,
        composition_policy: Mapping[str, Any] | None = None,
    ) -> DatasetSetRef:
        requested = tuple(requirements)
        dataset_set, missing = self._catalog.resolve(
            requested, composition_policy=composition_policy
        )
        if dataset_set is not None:
            return dataset_set
        if not acquire_missing:
            raise DataUnavailableError(missing)
        plan = self._catalog.plan(requested)
        acquired = await self.execute(plan, max_concurrency=max_concurrency)
        if composition_policy:
            return DatasetSetRef(
                acquired.members, composition_policy=composition_policy
            )
        return acquired

    async def publish(
        self,
        *,
        dataset_id: str,
        owner: str,
        kind: str,
        subject: str,
        events: Iterable[Mapping[str, Any]],
        product: str | None = None,
        source: str | None = None,
        venue: str | None = None,
        cadence: str | None = None,
        schema_version: str = "1",
        quality_status: str = "validated",
        reference_snapshot_id: str | None = None,
        lineage: Mapping[str, Any] | None = None,
        quality_report: Mapping[str, Any] | None = None,
        coverage_start_time_unix_nanos: int | None = None,
        coverage_end_time_unix_nanos: int | None = None,
    ) -> DatasetRef:
        return self._catalog.publish(
            dataset_id=dataset_id,
            owner=owner,
            kind=kind,
            subject=subject,
            events=events,
            product=product,
            source=source,
            venue=venue,
            cadence=cadence,
            schema_version=schema_version,
            quality_status=quality_status,
            reference_snapshot_id=reference_snapshot_id,
            lineage=lineage,
            quality_report=quality_report,
            coverage_start_time_unix_nanos=coverage_start_time_unix_nanos,
            coverage_end_time_unix_nanos=coverage_end_time_unix_nanos,
        )

    async def publish_derived(
        self,
        *,
        dataset_id: str,
        owner: str,
        kind: str,
        subject: str,
        events: Iterable[Mapping[str, Any]],
        parents: DatasetSetRef,
        derivation: str,
        availability_semantics: str,
        reference_snapshot_id: str | None = None,
        product: str | None = None,
        source: str = "derived",
        venue: str | None = None,
        cadence: str | None = None,
        schema_version: str = "1",
        quality_status: str = "validated",
        quality_report: Mapping[str, Any] | None = None,
        coverage_start_time_unix_nanos: int | None = None,
        coverage_end_time_unix_nanos: int | None = None,
    ) -> DatasetRef:
        """Publish a derived atomic Dataset bound to its exact parent set."""

        return self._catalog.publish_derived(
            dataset_id=dataset_id,
            owner=owner,
            kind=kind,
            subject=subject,
            events=events,
            parents=parents,
            derivation=derivation,
            availability_semantics=availability_semantics,
            reference_snapshot_id=reference_snapshot_id,
            product=product,
            source=source,
            venue=venue,
            cadence=cadence,
            schema_version=schema_version,
            quality_status=quality_status,
            quality_report=quality_report,
            coverage_start_time_unix_nanos=coverage_start_time_unix_nanos,
            coverage_end_time_unix_nanos=coverage_end_time_unix_nanos,
        )

    def read_plan(
        self,
        dataset_set: DatasetSetRef,
        *,
        start_time_unix_nanos: int | None = None,
        end_time_unix_nanos: int | None = None,
        kinds: tuple[str, ...] = (),
        replay_policy: Mapping[str, Any] | None = None,
    ) -> DatasetReadPlan:
        return self._readers.plan(
            dataset_set,
            start_time_unix_nanos=start_time_unix_nanos,
            end_time_unix_nanos=end_time_unix_nanos,
            kinds=kinds,
            replay_policy=replay_policy,
        )

    def snapshot(self, plan: DatasetReadPlan) -> DatasetSnapshot:
        return self._readers.snapshot(plan)

    def replay(self, plan: DatasetReadPlan) -> ReplayStream:
        return self._readers.replay(plan)

    def analytical(self, plan: DatasetReadPlan) -> DatasetAnalyticalView:
        """Open a lazy analytical view over the same shared read plan."""

        return self._readers.analytical(plan)

    def validate_trust(
        self,
        dataset_set: DatasetSetRef,
        *,
        required_kinds: tuple[str, ...] = (),
        require_point_in_time_policy: bool = True,
    ) -> DataTrustGateReport:
        """Produce Gate 1 evidence for one fixed Dataset composition."""

        return DataTrustGateApplication(self._catalog).evaluate(
            dataset_set,
            required_kinds=required_kinds,
            require_point_in_time_policy=require_point_in_time_policy,
        )

    def publish_trust_report(
        self,
        dataset_set: DatasetSetRef,
        *,
        required_kinds: tuple[str, ...] = (),
        require_point_in_time_policy: bool = True,
    ) -> DataTrustGateReport:
        """Evaluate and persist Gate 1 evidence under the bound Project."""

        return DataTrustGateApplication(self._catalog).publish(
            dataset_set,
            required_kinds=required_kinds,
            require_point_in_time_policy=require_point_in_time_policy,
        )

    def trust_report(self, composition_hash: str) -> Mapping[str, Any]:
        """Read persisted Gate 1 evidence by Dataset Set identity."""

        return self._application.trust_report(composition_hash)


__all__ = [
    "DataAcquisitionPlan",
    "DataCatalogClient",
    "DataClient",
    "DataRequirement",
    "DataUnavailableError",
    "DataTrustGateReport",
    "DatasetAnalyticalView",
    "DatasetDescription",
    "DatasetReadPlan",
    "DatasetRef",
    "DatasetSetRef",
    "DatasetSnapshot",
    "OptionMarketDataTarget",
    "ReplayStream",
]
