"""Workspace-scoped catalog and immutable atomic dataset publication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping
import uuid

from kairospy.system.apps.workspace.application import Workspace
from .models import (
    AcquisitionStep,
    DataAcquisitionPlan,
    DataRequirement,
    DatasetDescription,
    DatasetPartitionSummary,
    DatasetRef,
    DatasetSetRef,
    MissingCoverage,
)


_QUALITY_RANK = {"unvalidated": 0, "validated": 1, "trusted": 2}
_KIND_ALIASES = {"greek": "option-greeks", "greeks": "option-greeks"}


def normalize_kind(value: str) -> str:
    normalized = value.lower().replace("_", "-")
    return _KIND_ALIASES.get(normalized, normalized)


def observation_kind(event: Mapping[str, Any]) -> str:
    explicit = event.get("kind")
    if isinstance(explicit, str) and explicit:
        return normalize_kind(explicit)
    if len(event) == 1:
        return normalize_kind(str(next(iter(event))))
    raise ValueError("canonical event must contain kind or one observation envelope")


def event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    if "kind" in event:
        return event
    if len(event) == 1:
        payload = next(iter(event.values()))
        if isinstance(payload, Mapping):
            return payload
    raise ValueError("canonical event payload must be a mapping")


def event_time(event: Mapping[str, Any]) -> int | None:
    payload = event_payload(event)
    for name in (
        "observed_at_unix_nanos",
        "available_at_unix_nanos",
        "effective_at_unix_nanos",
    ):
        value = payload.get(name)
        if value is not None:
            return int(value)
    return None


def _canonical_line(event: Mapping[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe_key(value: str) -> str:
    prefix = "".join(character if character.isalnum() else "-" for character in value)
    prefix = "-".join(part for part in prefix.split("-") if part)[:48] or "dataset"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _public_metadata(value: Any) -> Any:
    """Remove physical location fields from public manifest metadata."""

    if isinstance(value, Mapping):
        return {
            str(key): _public_metadata(item)
            for key, item in value.items()
            if str(key).lower() not in {"file", "path", "canonical"}
            and not str(key).lower().endswith("_path")
        }
    if isinstance(value, list):
        return [_public_metadata(item) for item in value]
    return value


def dataset_id_for_requirement(requirement: DataRequirement) -> str:
    """Return the deterministic logical target identity for an acquisition."""

    identity = {
        "owner": requirement.owner,
        "kind": requirement.kind,
        "subject": requirement.subject,
        "product": requirement.product,
        "source": requirement.source,
        "venue": requirement.venue,
        "cadence": requirement.cadence,
        "start": requirement.start_time_unix_nanos,
        "end": requirement.end_time_unix_nanos,
        "parameters": dict(requirement.parameters),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return f"{requirement.owner}.{requirement.kind}/{requirement.subject}/{digest}"


@dataclass(frozen=True, slots=True)
class DatasetCatalogApplication:
    """Unified logical catalog delegating physical storage to dataset owners."""

    workspace: Workspace

    @property
    def catalog_path(self) -> Path:
        return self.workspace.paths.child("state", "data", "catalog.json")

    @property
    def staging_root(self) -> Path:
        return self.workspace.paths.child("state", "data", "staging")

    def list(
        self,
        *,
        owner: str | None = None,
        kind: str | None = None,
        subject: str | None = None,
    ) -> tuple[DatasetRef, ...]:
        refs = tuple(self._entry_ref(entry) for entry in self._entries())
        return tuple(
            ref
            for ref in refs
            if (owner is None or ref.owner == owner)
            and (kind is None or ref.kind == kind)
            and (subject is None or ref.subject == subject)
        )

    def inspect(
        self, dataset: DatasetRef | str, version: str | None = None
    ) -> DatasetRef:
        if isinstance(dataset, DatasetRef):
            dataset_id = dataset.dataset_id
            version = dataset.version
        else:
            dataset_id = dataset
        matches = [
            ref
            for ref in self.list()
            if ref.dataset_id == dataset_id
            and (version is None or ref.version == version)
        ]
        if not matches:
            suffix = f"@{version}" if version is not None else ""
            raise FileNotFoundError(f"dataset does not exist: {dataset_id}{suffix}")
        return sorted(matches, key=lambda ref: ref.version)[-1]

    def describe(
        self, dataset: DatasetRef | str, version: str | None = None
    ) -> DatasetDescription:
        """Return reviewable lineage and quality metadata without storage paths."""

        ref = self.inspect(dataset, version)
        for entry in self._entries():
            if self._entry_ref(entry).identity != ref.identity:
                continue
            manifest_path = self.workspace.paths.root / entry["manifest"]
            paths = self._validate_manifest(manifest_path, ref)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            physical = manifest["physical"]
            raw_partitions = physical.get("partitions")
            if raw_partitions is None:
                raw_partitions = [physical]
            partitions = tuple(
                DatasetPartitionSummary(
                    key=str(partition.get("key") or f"partition-{index}"),
                    event_count=int(
                        partition.get("event_count")
                        if partition.get("event_count") is not None
                        else sum(1 for _ in paths[index].open(encoding="utf-8"))
                    ),
                    content_hash=str(partition.get("sha256") or ""),
                    format=str(partition.get("format") or physical.get("format") or ""),
                )
                for index, partition in enumerate(raw_partitions)
            )
            if len(paths) != len(partitions):
                raise ValueError("dataset partition metadata does not match content")
            return DatasetDescription(
                ref=ref,
                lineage=_public_metadata(manifest.get("lineage") or {}),
                quality_report=_public_metadata(manifest.get("quality_report") or {}),
                partitions=partitions,
            )
        raise FileNotFoundError(f"dataset is not registered: {ref.identity}")

    def publish(
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
        """Validate and atomically publish one immutable logical dataset."""
        return self.publish_partitions(
            dataset_id=dataset_id,
            owner=owner,
            kind=kind,
            subject=subject,
            partitions=(("canonical", events),),
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

    def publish_derived(
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
        """Publish a derived Dataset with mandatory reproducibility lineage."""

        if not derivation.strip():
            raise ValueError("derived Dataset derivation is required")
        if availability_semantics not in {"known", "unknown", "derived"}:
            raise ValueError("derived Dataset availability semantics are invalid")
        for parent in parents.members:
            if self.inspect(parent.dataset_id, parent.version) != parent:
                raise ValueError(
                    f"derived Dataset parent does not match Catalog: {parent.identity}"
                )
        if kind == "option-greeks" and not reference_snapshot_id:
            raise ValueError("derived option Greeks require a Reference snapshot")
        return self.publish(
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
            lineage={
                "derivation": derivation,
                "availability_semantics": availability_semantics,
                "parent_composition_hash": parents.composition_hash,
                "parents": [member.as_dict() for member in parents.members],
                "reference_snapshot_id": reference_snapshot_id,
            },
            quality_report=quality_report,
            coverage_start_time_unix_nanos=coverage_start_time_unix_nanos,
            coverage_end_time_unix_nanos=coverage_end_time_unix_nanos,
        )

    def publish_partitions(
        self,
        *,
        dataset_id: str,
        owner: str,
        kind: str,
        subject: str,
        partitions: Iterable[tuple[str, Iterable[Mapping[str, Any]]]],
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
        """Atomically publish one Dataset containing deterministic partitions."""
        if quality_status not in _QUALITY_RANK:
            raise ValueError(f"unsupported quality status: {quality_status}")
        normalized_kind = normalize_kind(kind)
        normalized_partitions: list[tuple[str, list[dict[str, Any]]]] = []
        seen_keys: set[str] = set()
        times: list[int] = []
        for partition_key, partition_events in partitions:
            key = partition_key.strip()
            if not key or key in seen_keys:
                raise ValueError("dataset partition keys must be non-empty and unique")
            seen_keys.add(key)
            records = [dict(event) for event in partition_events]
            for index, event in enumerate(records):
                actual = observation_kind(event)
                if actual != normalized_kind:
                    raise ValueError(
                        f"atomic dataset kind mismatch in {key} at event {index}: "
                        f"expected={normalized_kind}, actual={actual}"
                    )
            indexed = list(enumerate(records))
            indexed.sort(
                key=lambda item: (
                    event_time(item[1]) is None,
                    event_time(item[1]) or 0,
                    _canonical_line(item[1]),
                    item[0],
                )
            )
            records = [event for _, event in indexed]
            times.extend(
                time for event in records if (time := event_time(event)) is not None
            )
            normalized_partitions.append((key, records))
        if not normalized_partitions:
            raise ValueError("dataset publication requires at least one partition")
        normalized_partitions.sort(key=lambda item: item[0])
        logical: dict[str, Any] = {
            "dataset_id": dataset_id,
            "owner": owner,
            "kind": normalized_kind,
            "subject": subject,
            "product": product,
            "source": source,
            "venue": venue,
            "cadence": cadence,
            "schema_version": schema_version,
            "quality_status": quality_status,
            "reference_snapshot_id": reference_snapshot_id,
            "lineage": dict(lineage or {}),
            "quality_report": dict(quality_report or {}),
            "coverage_start_time_unix_nanos": coverage_start_time_unix_nanos,
            "coverage_end_time_unix_nanos": coverage_end_time_unix_nanos,
        }
        if (
            len(normalized_partitions) == 1
            and normalized_partitions[0][0] == "canonical"
        ):
            logical["events"] = normalized_partitions[0][1]
        else:
            logical["partitions"] = [
                {"key": key, "events": records}
                for key, records in normalized_partitions
            ]
        content_hash = hashlib.sha256(
            json.dumps(
                logical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        version = content_hash[:16]
        ref = DatasetRef(
            dataset_id=dataset_id,
            version=version,
            content_hash=content_hash,
            owner=owner,
            kind=normalized_kind,
            subject=subject,
            start_time_unix_nanos=(
                coverage_start_time_unix_nanos
                if coverage_start_time_unix_nanos is not None
                else (min(times) if times else None)
            ),
            end_time_unix_nanos=(
                coverage_end_time_unix_nanos
                if coverage_end_time_unix_nanos is not None
                else (max(times) if times else None)
            ),
            event_count=sum(len(records) for _, records in normalized_partitions),
            schema_version=schema_version,
            product=product,
            source=source,
            venue=venue,
            cadence=cadence,
            quality_status=quality_status,
            reference_snapshot_id=reference_snapshot_id,
        )
        existing = [
            entry
            for entry in self._entries()
            if entry["ref"]["dataset_id"] == ref.dataset_id
            and entry["ref"]["version"] == ref.version
        ]
        if existing:
            registered = self._entry_ref(existing[0])
            if registered.content_hash != ref.content_hash:
                raise RuntimeError("immutable dataset version has conflicting content")
            return registered

        owner_root = self.workspace.paths.child("data", owner, "datasets")
        final = owner_root / _safe_key(dataset_id) / version
        stage = self.staging_root / (
            f"{_safe_key(dataset_id)}-{version}-{uuid.uuid4().hex}"
        )
        stage.mkdir(parents=True, exist_ok=False)
        partition_root = stage / "partitions"
        partition_root.mkdir()
        physical_partitions = []
        for ordinal, (key, records) in enumerate(normalized_partitions):
            relative = Path("partitions") / f"{ordinal:06d}-{_safe_key(key)}.jsonl"
            data_path = stage / relative
            data_path.write_text(
                "".join(_canonical_line(event) + "\n" for event in records),
                encoding="utf-8",
            )
            physical_partitions.append(
                {
                    "key": key,
                    "canonical": relative.as_posix(),
                    "format": "jsonl",
                    "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
                    "event_count": len(records),
                }
            )
        manifest = {
            "manifest_version": 1,
            "ref": ref.as_dict(),
            "lineage": dict(lineage or {}),
            "quality_report": dict(quality_report or {}),
            "physical": {
                "format": "partitioned-jsonl-v1",
                "partitions": physical_partitions,
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return self._commit_stage(stage, final, ref)

    def _commit_stage(self, stage: Path, final: Path, ref: DatasetRef) -> DatasetRef:
        """Serialize the rename + Catalog commit and recover interrupted commits."""

        lock_path = self.workspace.paths.child("state", "data", "catalog.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                entries = self._entries()
                for entry in entries:
                    registered = self._entry_ref(entry)
                    if registered.identity != ref.identity:
                        continue
                    if registered != ref:
                        raise RuntimeError(
                            "immutable dataset version has conflicting content"
                        )
                    self._validate_manifest(
                        self.workspace.paths.root / entry["manifest"], registered
                    )
                    shutil.rmtree(stage)
                    return registered
                final.parent.mkdir(parents=True, exist_ok=True)
                manifest_path = final / "manifest.json"
                if final.exists():
                    shutil.rmtree(stage)
                    self._validate_manifest(manifest_path, ref)
                else:
                    os.replace(stage, final)
                entries.append(
                    {
                        "ref": ref.as_dict(),
                        "manifest": str(
                            manifest_path.relative_to(self.workspace.paths.root)
                        ),
                    }
                )
                self._write_entries(entries)
                return ref
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def resolve(
        self,
        requirements: Iterable[DataRequirement],
        *,
        composition_policy: Mapping[str, Any] | None = None,
    ) -> tuple[DatasetSetRef | None, tuple[MissingCoverage, ...]]:
        selected: list[DatasetRef] = []
        missing: list[MissingCoverage] = []
        for requirement in tuple(requirements):
            candidates = [
                ref
                for ref in self.list(
                    owner=requirement.owner,
                    kind=requirement.kind,
                    subject=requirement.subject,
                )
                if self._matches(ref, requirement)
            ]
            covering = [ref for ref in candidates if self._covers(ref, requirement)]
            if not covering:
                reason = "no matching local dataset"
                if candidates:
                    reason = "matching datasets do not cover the requested interval or quality"
                missing.append(MissingCoverage(requirement, reason, tuple(candidates)))
                continue
            selected.append(
                sorted(
                    covering,
                    key=lambda ref: (
                        _QUALITY_RANK.get(ref.quality_status, -1),
                        ref.end_time_unix_nanos or -1,
                        ref.version,
                    ),
                )[-1]
            )
        if missing:
            return None, tuple(missing)
        unique = {ref.identity: ref for ref in selected}
        ordered = tuple(unique[key] for key in sorted(unique))
        return DatasetSetRef(ordered, composition_policy=composition_policy or {}), ()

    def plan(self, requirements: Iterable[DataRequirement]) -> DataAcquisitionPlan:
        requested = tuple(requirements)
        dataset_set, missing = self.resolve(requested)
        satisfied = (
            dataset_set.members
            if dataset_set is not None
            else tuple(
                ref
                for requirement in requested
                for ref in self.list(
                    owner=requirement.owner,
                    kind=requirement.kind,
                    subject=requirement.subject,
                )
                if self._matches(ref, requirement) and self._covers(ref, requirement)
            )
        )
        steps = tuple(
            AcquisitionStep(
                operation="acquire-normalize-validate-publish",
                owner=item.requirement.owner,
                requirement=item.requirement,
                provider=item.requirement.source,
                capability=(
                    f"historical-{item.requirement.kind}"
                    if item.requirement.owner == "market"
                    else f"reference-{item.requirement.kind}"
                ),
                credential_id=item.requirement.parameters.get("credential_id"),
                target_dataset_id=dataset_id_for_requirement(item.requirement),
                partitioning=(
                    ("event-date",)
                    if item.requirement.owner == "market"
                    else ("as-of",)
                ),
                estimated_requests=None,
                estimated_bytes=None,
                estimated_cost="unknown; governed by provider entitlement",
                entitlement="provider account must permit the requested history",
                degradation="none; missing facts remain an explicit error",
                blocked_reason=(
                    None
                    if item.requirement.source
                    else "no provider/source was selected"
                ),
                detail="provider capability must be selected by owner composition",
            )
            for item in missing
        )
        serializable = {
            "project_id": self.workspace.workspace_id,
            "requirements": [asdict(requirement) for requirement in requested],
            "satisfied": [ref.as_dict() for ref in satisfied],
            "missing": [item.reason for item in missing],
            "steps": [
                {
                    "operation": step.operation,
                    "owner": step.owner,
                    "provider": step.provider,
                    "capability": step.capability,
                    "credential_id": step.credential_id,
                    "target_dataset_id": step.target_dataset_id,
                    "phases": list(step.phases),
                    "partitioning": list(step.partitioning),
                    "estimated_requests": step.estimated_requests,
                    "estimated_bytes": step.estimated_bytes,
                    "estimated_cost": step.estimated_cost,
                    "entitlement": step.entitlement,
                    "degradation": step.degradation,
                    "blocked_reason": step.blocked_reason,
                }
                for step in steps
            ],
        }
        plan_hash = hashlib.sha256(
            json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return DataAcquisitionPlan(
            project_id=self.workspace.workspace_id,
            requirements=requested,
            satisfied=tuple(dict.fromkeys(satisfied)),
            missing=missing,
            steps=steps,
            plan_hash=plan_hash,
        )

    def data_path(self, ref: DatasetRef) -> Path:
        paths = self.data_paths(ref)
        if len(paths) != 1:
            raise ValueError(
                "dataset has multiple internal partitions; use the logical reader"
            )
        return paths[0]

    def data_paths(self, ref: DatasetRef) -> tuple[Path, ...]:
        for entry in self._entries():
            candidate = self._entry_ref(entry)
            if candidate.identity != ref.identity:
                continue
            manifest_path = self.workspace.paths.root / entry["manifest"]
            return self._validate_manifest(manifest_path, ref)
        raise FileNotFoundError(f"dataset is not registered: {ref.identity}")

    @staticmethod
    def _validate_manifest(manifest_path: Path, ref: DatasetRef) -> tuple[Path, ...]:
        if not manifest_path.is_file():
            raise FileNotFoundError(f"dataset manifest is missing: {ref.identity}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registered = DatasetRef.from_dict(manifest["ref"])
        if registered != ref:
            raise ValueError("dataset reference does not match its manifest")
        physical = manifest["physical"]
        raw_partitions = physical.get("partitions")
        if raw_partitions is None:
            raw_partitions = [physical]
        paths = []
        count = 0
        for partition in raw_partitions:
            path = manifest_path.parent / partition["canonical"]
            if not path.is_file():
                raise FileNotFoundError(f"dataset content is missing: {ref.identity}")
            expected_hash = partition.get("sha256")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected_hash != actual_hash:
                raise ValueError(
                    f"dataset physical content hash mismatch: {ref.identity}"
                )
            count += int(partition.get("event_count", 0))
            paths.append(path)
        if (
            all("event_count" in item for item in raw_partitions)
            and count != ref.event_count
        ):
            raise ValueError(f"dataset partition event count mismatch: {ref.identity}")
        return tuple(paths)

    @staticmethod
    def _matches(ref: DatasetRef, requirement: DataRequirement) -> bool:
        return all(
            expected is None or actual == expected
            for actual, expected in (
                (ref.product, requirement.product),
                (ref.source, requirement.source),
                (ref.venue, requirement.venue),
                (ref.cadence, requirement.cadence),
                (ref.schema_version, requirement.schema_version),
            )
        )

    @staticmethod
    def _covers(ref: DatasetRef, requirement: DataRequirement) -> bool:
        if _QUALITY_RANK.get(ref.quality_status, -1) < _QUALITY_RANK.get(
            requirement.minimum_quality, 1
        ):
            return False
        if requirement.start_time_unix_nanos is not None and (
            ref.start_time_unix_nanos is None
            or ref.start_time_unix_nanos > requirement.start_time_unix_nanos
        ):
            return False
        if requirement.end_time_unix_nanos is not None and (
            ref.end_time_unix_nanos is None
            or ref.end_time_unix_nanos < requirement.end_time_unix_nanos
        ):
            return False
        return True

    def _entries(self) -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            return []
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entries = value.get("datasets", []) if isinstance(value, Mapping) else []
        if not isinstance(entries, list):
            raise ValueError("dataset catalog datasets must be a list")
        return [dict(entry) for entry in entries]

    @staticmethod
    def _entry_ref(entry: Mapping[str, Any]) -> DatasetRef:
        ref = entry.get("ref")
        if not isinstance(ref, Mapping):
            raise ValueError("dataset catalog entry is missing ref")
        return DatasetRef.from_dict(ref)

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"catalog_version": 1, "datasets": entries},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.catalog_path)
