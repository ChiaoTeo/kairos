"""Public, storage-independent dataset contracts.

The contracts in this module describe logical data.  They intentionally do
not expose Parquet files, JSONL files, partitions, or replay materializations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Mapping


def _required(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DataRequirement:
    """A logical dataset requirement, independent of local storage layout."""

    owner: str
    kind: str
    subject: str
    start_time_unix_nanos: int | None = None
    end_time_unix_nanos: int | None = None
    product: str | None = None
    source: str | None = None
    venue: str | None = None
    cadence: str | None = None
    schema_version: str | None = None
    minimum_quality: str = "validated"
    parameters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _required("owner", self.owner))
        object.__setattr__(self, "kind", _required("kind", self.kind))
        object.__setattr__(self, "subject", _required("subject", self.subject))
        if (
            self.start_time_unix_nanos is not None
            and self.end_time_unix_nanos is not None
            and self.start_time_unix_nanos > self.end_time_unix_nanos
        ):
            raise ValueError("dataset requirement start must not be after end")
        for name, value in self.parameters.items():
            if not str(name).strip() or not str(value).strip():
                raise ValueError("dataset requirement parameters must be non-empty")
            normalized = str(name).lower().replace("-", "_")
            if normalized in {
                "api_key",
                "apikey",
                "access_token",
                "refresh_token",
                "password",
                "secret",
            }:
                raise ValueError(
                    "dataset requirements may reference credential_id but cannot contain secrets"
                )


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """An immutable reference to one published atomic dataset version."""

    dataset_id: str
    version: str
    content_hash: str
    owner: str
    kind: str
    subject: str
    start_time_unix_nanos: int | None
    end_time_unix_nanos: int | None
    event_count: int
    schema_version: str = "1"
    product: str | None = None
    source: str | None = None
    venue: str | None = None
    cadence: str | None = None
    quality_status: str = "validated"
    reference_snapshot_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "version",
            "content_hash",
            "owner",
            "kind",
            "subject",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.event_count < 0:
            raise ValueError("event_count cannot be negative")
        if (
            self.start_time_unix_nanos is not None
            and self.end_time_unix_nanos is not None
            and self.start_time_unix_nanos > self.end_time_unix_nanos
        ):
            raise ValueError("dataset coverage start must not be after end")

    @property
    def identity(self) -> str:
        return f"{self.dataset_id}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "owner": self.owner,
            "kind": self.kind,
            "subject": self.subject,
            "start_time_unix_nanos": self.start_time_unix_nanos,
            "end_time_unix_nanos": self.end_time_unix_nanos,
            "event_count": self.event_count,
            "schema_version": self.schema_version,
            "product": self.product,
            "source": self.source,
            "venue": self.venue,
            "cadence": self.cadence,
            "quality_status": self.quality_status,
            "reference_snapshot_id": self.reference_snapshot_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetRef":
        return cls(
            **{name: value[name] for name in cls.__dataclass_fields__ if name in value}
        )


@dataclass(frozen=True, slots=True)
class DatasetPartitionSummary:
    """Storage-independent summary of one internal Dataset partition."""

    key: str
    event_count: int
    content_hash: str
    format: str

    def __post_init__(self) -> None:
        for name in ("key", "content_hash", "format"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.event_count < 0:
            raise ValueError("partition event_count cannot be negative")


@dataclass(frozen=True, slots=True)
class DatasetDescription:
    """Reviewable Dataset metadata without physical storage locations."""

    ref: DatasetRef
    lineage: Mapping[str, Any]
    quality_report: Mapping[str, Any]
    partitions: tuple[DatasetPartitionSummary, ...]

    def __post_init__(self) -> None:
        if not self.partitions:
            raise ValueError("dataset description requires at least one partition")
        if sum(item.event_count for item in self.partitions) != self.ref.event_count:
            raise ValueError("dataset partition summary does not match event count")


@dataclass(frozen=True, slots=True)
class DatasetSetRef:
    """An immutable composition of atomic datasets."""

    members: tuple[DatasetRef, ...]
    composition_hash: str = ""
    composition_policy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError("dataset set requires at least one member")
        identities = [member.identity for member in self.members]
        if len(set(identities)) != len(identities):
            raise ValueError("dataset set cannot contain duplicate members")
        calculated = _canonical_hash(
            {
                "members": [member.as_dict() for member in self.members],
                "composition_policy": dict(self.composition_policy),
            }
        )
        if self.composition_hash and self.composition_hash != calculated:
            raise ValueError("dataset set composition hash does not match members")
        object.__setattr__(self, "composition_hash", calculated)

    def as_dict(self) -> dict[str, Any]:
        return {
            "members": [member.as_dict() for member in self.members],
            "composition_hash": self.composition_hash,
            "composition_policy": dict(self.composition_policy),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSetRef":
        raw_members = value.get("members", ())
        if not isinstance(raw_members, (list, tuple)):
            raise ValueError("dataset set members must be a sequence")
        return cls(
            members=tuple(DatasetRef.from_dict(member) for member in raw_members),
            composition_hash=str(value.get("composition_hash", "")),
            composition_policy=dict(value.get("composition_policy", {})),
        )


@dataclass(frozen=True, slots=True)
class MissingCoverage:
    requirement: DataRequirement
    reason: str
    available: tuple[DatasetRef, ...] = ()


class DataUnavailableError(LookupError):
    """Raised when read-only resolution cannot satisfy all requirements."""

    def __init__(self, missing: tuple[MissingCoverage, ...]) -> None:
        self.missing = missing
        detail = "; ".join(
            f"{item.requirement.owner}.{item.requirement.kind}/"
            f"{item.requirement.subject}: {item.reason}"
            for item in missing
        )
        super().__init__(f"dataset requirements are not locally satisfied: {detail}")


@dataclass(frozen=True, slots=True)
class AcquisitionStep:
    operation: str
    owner: str
    requirement: DataRequirement
    provider: str | None = None
    capability: str | None = None
    credential_id: str | None = None
    target_dataset_id: str | None = None
    phases: tuple[str, ...] = (
        "acquire",
        "normalize",
        "validate",
        "prepare",
        "publish",
    )
    partitioning: tuple[str, ...] = ()
    estimated_requests: int | None = None
    estimated_bytes: int | None = None
    estimated_cost: str | None = None
    entitlement: str | None = None
    degradation: str | None = None
    blocked_reason: str | None = None
    status: str = "planned"
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DataAcquisitionPlan:
    project_id: str
    requirements: tuple[DataRequirement, ...]
    satisfied: tuple[DatasetRef, ...]
    missing: tuple[MissingCoverage, ...]
    steps: tuple[AcquisitionStep, ...]
    plan_hash: str

    @property
    def executable(self) -> bool:
        return bool(self.steps) and all(
            step.provider and step.blocked_reason is None for step in self.steps
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a reviewable plan without provider secrets or physical paths."""

        return {
            "project_id": self.project_id,
            "requirements": [asdict(item) for item in self.requirements],
            "satisfied": [item.as_dict() for item in self.satisfied],
            "missing": [
                {
                    "requirement": asdict(item.requirement),
                    "reason": item.reason,
                    "available": [ref.as_dict() for ref in item.available],
                }
                for item in self.missing
            ],
            "steps": [asdict(item) for item in self.steps],
            "plan_hash": self.plan_hash,
            "executable": self.executable,
        }


@dataclass(frozen=True, slots=True)
class DatasetReadPlan:
    """Shared logical read plan used by snapshot and replay consumers."""

    dataset_set: DatasetSetRef
    start_time_unix_nanos: int | None = None
    end_time_unix_nanos: int | None = None
    kinds: tuple[str, ...] = ()
    replay_policy: Mapping[str, Any] = field(default_factory=dict)
    plan_hash: str = ""

    def __post_init__(self) -> None:
        if (
            self.start_time_unix_nanos is not None
            and self.end_time_unix_nanos is not None
            and self.start_time_unix_nanos > self.end_time_unix_nanos
        ):
            raise ValueError("read plan start must not be after end")
        calculated = _canonical_hash(
            {
                "dataset_set": self.dataset_set.as_dict(),
                "start_time_unix_nanos": self.start_time_unix_nanos,
                "end_time_unix_nanos": self.end_time_unix_nanos,
                "kinds": list(self.kinds),
                "replay_policy": dict(self.replay_policy),
            }
        )
        if self.plan_hash and self.plan_hash != calculated:
            raise ValueError("dataset read plan hash does not match its inputs")
        object.__setattr__(self, "plan_hash", calculated)
