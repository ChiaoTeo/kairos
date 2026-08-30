"""Typed composite results owned by the Reference Python contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal, TypeAlias

from kairospy.primitives.reference import InstrumentIdRead, ReferenceSourceIdRead
from kairospy.primitives.time import GenerationRead, SequenceRead

ReferenceHealthStatus: TypeAlias = Literal["ready", "degraded"]
ReferenceProviderStatus: TypeAlias = Literal[
    "ready", "syncing", "degraded", "paused", "disabled"
]


class ReferenceRuntimeState(StrEnum):
    STARTING = "starting"
    SYNCING = "syncing"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ReferenceAppPhase(StrEnum):
    BOOTING = "booting"
    LOADING = "loading"
    SERVING = "serving"
    TICKING = "ticking"
    SCANNING = "scanning"
    RECONCILING = "reconciling"
    PUBLISHING = "publishing"
    DEGRADED = "degraded"
    STOPPING = "stopping"


class ReferenceCatalogReadiness(StrEnum):
    EMPTY = "empty"
    SYNCING = "syncing"
    READY = "ready"
    DEGRADED = "degraded"
    INVALID = "invalid"


class ReferenceSourceKind(StrEnum):
    UNKNOWN = "unknown"
    GLOBAL = "global"
    SCOPED = "scoped"
    MANUAL_CURATED = "manual_curated"


class ReferenceSourcePhase(StrEnum):
    DISABLED = "disabled"
    REGISTERED = "registered"
    IDLE = "idle"
    SCANNING = "scanning"
    PROMOTING = "promoting"
    SYNCING = "syncing"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    PAUSED = "paused"


class ReferenceSourceDesiredState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    PAUSED = "paused"
    REMOVED = "removed"


class ReferenceSourceSyncPolicy(StrEnum):
    FULL_SNAPSHOT = "full_snapshot"
    PAGED_SNAPSHOT = "paged_snapshot"
    SCOPED_SNAPSHOT = "scoped_snapshot"
    INCREMENTAL_DELTA = "incremental_delta"
    MANUAL_CURATED = "manual_curated"


class ReferenceSourceScopeKind(StrEnum):
    GLOBAL = "global"
    PROVIDER_CATALOG = "provider_catalog"
    UNDERLYING_INSTRUMENT = "underlying_instrument"
    COVERAGE = "coverage"
    CUSTOM = "custom"


class ReferenceSourceProgressKind(StrEnum):
    UNKNOWN = "unknown"
    COMPLETE = "complete"
    PAGED = "paged"
    SCOPED = "scoped"


class ReferenceDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ReferenceRuntimeError:
    code: str
    retryable: bool
    message: str

    @classmethod
    def from_mapping(cls, value: object, name: str) -> ReferenceRuntimeError:
        row = _mapping(value, name)
        return cls(
            _text(row.get("code"), "code"),
            _boolean(row.get("retryable"), "retryable"),
            _text(row.get("message"), "message"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceTickBudget:
    max_sources_per_tick: int
    max_batches_per_source: int
    max_records_per_batch: int | None
    max_wall_clock_millis: int | None
    max_publications_per_tick: int | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceTickBudget:
        row = _mapping(value, "Reference source tick budget")
        return cls(
            _integer(row.get("max_sources_per_tick", 1), "max_sources_per_tick"),
            _integer(row.get("max_batches_per_source", 1), "max_batches_per_source"),
            _optional_integer(
                row.get("max_records_per_batch"), "max_records_per_batch"
            ),
            _optional_integer(
                row.get("max_wall_clock_millis"), "max_wall_clock_millis"
            ),
            _optional_integer(
                row.get("max_publications_per_tick"), "max_publications_per_tick"
            ),
        )


@dataclass(frozen=True, slots=True)
class ReferenceAppRuntimeStatus:
    phase: ReferenceAppPhase
    actor_id: str
    source_id: str
    refresh_interval_millis: int
    last_tick_started_unix_nanos: int | None
    last_tick_finished_unix_nanos: int | None
    last_tick_duration_millis: int | None
    next_tick_due_unix_nanos: int | None
    active_work_item_count: int
    queued_work_item_count: int
    last_error: ReferenceRuntimeError | None
    tick_budget: ReferenceSourceTickBudget

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceAppRuntimeStatus:
        row = _mapping(value, "Reference app runtime status")
        error = row.get("last_error")
        return cls(
            ReferenceAppPhase(_text(row.get("phase"), "phase")),
            _text(row.get("actor_id"), "actor_id"),
            _text(row.get("source_id"), "source_id"),
            _integer(row.get("refresh_interval_millis"), "refresh_interval_millis"),
            _optional_integer(
                row.get("last_tick_started_unix_nanos"),
                "last_tick_started_unix_nanos",
            ),
            _optional_integer(
                row.get("last_tick_finished_unix_nanos"),
                "last_tick_finished_unix_nanos",
            ),
            _optional_integer(
                row.get("last_tick_duration_millis"), "last_tick_duration_millis"
            ),
            _optional_integer(
                row.get("next_tick_due_unix_nanos"), "next_tick_due_unix_nanos"
            ),
            _integer(row.get("active_work_item_count", 0), "active_work_item_count"),
            _integer(row.get("queued_work_item_count", 0), "queued_work_item_count"),
            (
                None
                if error is None
                else ReferenceRuntimeError.from_mapping(
                    error, "Reference app runtime error"
                )
            ),
            ReferenceSourceTickBudget.from_mapping(row.get("tick_budget", {})),
        )


@dataclass(frozen=True, slots=True)
class ReferenceCatalogIntegrityStatus:
    degraded: bool
    missing_equity_market_count: int
    legacy_exchange_market_id_count: int
    legacy_exchange_listing_id_count: int
    option_listing_count: int
    option_market_count: int

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceCatalogIntegrityStatus:
        row = _mapping(value, "Reference catalog integrity status")
        return cls(
            _boolean(row.get("degraded", False), "degraded"),
            _integer(
                row.get("missing_equity_market_count", 0),
                "missing_equity_market_count",
            ),
            _integer(
                row.get("legacy_exchange_market_id_count", 0),
                "legacy_exchange_market_id_count",
            ),
            _integer(
                row.get("legacy_exchange_listing_id_count", 0),
                "legacy_exchange_listing_id_count",
            ),
            _integer(row.get("option_listing_count", 0), "option_listing_count"),
            _integer(row.get("option_market_count", 0), "option_market_count"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceCatalogRuntimeStatus:
    readiness: ReferenceCatalogReadiness
    generation: int
    event_sequence: int
    committed_at_unix_nanos: int
    exchange_count: int
    asset_count: int
    instrument_count: int
    listing_count: int
    market_count: int
    active_market_count: int
    lifecycle_event_count: int
    integrity: ReferenceCatalogIntegrityStatus

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceCatalogRuntimeStatus:
        row = _mapping(value, "Reference catalog runtime status")
        return cls(
            ReferenceCatalogReadiness(_text(row.get("readiness"), "readiness")),
            _integer(row.get("generation"), "generation"),
            _integer(row.get("event_sequence"), "event_sequence"),
            _integer(row.get("committed_at_unix_nanos", 0), "committed_at_unix_nanos"),
            _integer(row.get("exchange_count", 0), "exchange_count"),
            _integer(row.get("asset_count", 0), "asset_count"),
            _integer(row.get("instrument_count", 0), "instrument_count"),
            _integer(row.get("listing_count", 0), "listing_count"),
            _integer(row.get("market_count"), "market_count"),
            _integer(row.get("active_market_count", 0), "active_market_count"),
            _integer(row.get("lifecycle_event_count", 0), "lifecycle_event_count"),
            ReferenceCatalogIntegrityStatus.from_mapping(row.get("integrity", {})),
        )


@dataclass(frozen=True, slots=True)
class ReferencePublicationRuntimeStatus:
    pending_publication_count: int
    backlog_degraded: bool
    batch_limit: int
    oldest_pending_event_id: str | None
    last_error: ReferenceRuntimeError | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferencePublicationRuntimeStatus:
        row = _mapping(value, "Reference publication runtime status")
        error = row.get("last_error")
        return cls(
            _integer(row.get("pending_publication_count"), "pending_publication_count"),
            _boolean(row.get("backlog_degraded", False), "backlog_degraded"),
            _integer(row.get("batch_limit", 0), "batch_limit"),
            _optional_text(
                row.get("oldest_pending_event_id"), "oldest_pending_event_id"
            ),
            (
                None
                if error is None
                else ReferenceRuntimeError.from_mapping(
                    error, "Reference publication runtime error"
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ReferenceCoverageRuntimeStatus:
    option_underlyings: tuple[InstrumentIdRead, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceCoverageRuntimeStatus:
        row = _mapping(value, "Reference coverage runtime status")
        values = row.get("option_underlyings", [])
        if not isinstance(values, list):
            raise ValueError("Reference option underlyings must be a list")
        return cls(
            tuple(InstrumentIdRead(_text(item, "option_underlying")) for item in values)
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceScope:
    kind: ReferenceSourceScopeKind
    id: str | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceScope:
        row = _mapping(value, "Reference source scope")
        return cls(
            ReferenceSourceScopeKind(_text(row.get("kind", "global"), "scope.kind")),
            _optional_text(row.get("id"), "scope.id"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceProgress:
    kind: ReferenceSourceProgressKind
    pages_done: int | None
    pages_total: int | None
    records_seen: int | None
    records_changed: int | None
    scope_id: str | None
    scope_kind: str | None
    cursor_present: bool | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceProgress:
        row = _mapping(value, "Reference source progress")
        return cls(
            ReferenceSourceProgressKind(_text(row.get("kind"), "progress.kind")),
            _optional_integer(row.get("pages_done"), "pages_done"),
            _optional_integer(row.get("pages_total"), "pages_total"),
            _optional_integer(row.get("records_seen"), "records_seen"),
            _optional_integer(row.get("records_changed"), "records_changed"),
            _optional_text(row.get("scope_id"), "scope_id"),
            _optional_text(row.get("scope_kind"), "scope_kind"),
            _optional_boolean(row.get("cursor_present"), "cursor_present"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceWorkItem:
    work_item_id: str | None
    scope_id: str | None
    scope_kind: str | None
    cursor_present: bool | None
    skip_reason: str | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceWorkItem:
        row = _mapping(value, "Reference source work item")
        return cls(
            _optional_text(row.get("work_item_id"), "work_item_id"),
            _optional_text(row.get("scope_id"), "work_item.scope_id"),
            _optional_text(row.get("scope_kind"), "work_item.scope_kind"),
            _optional_boolean(row.get("cursor_present"), "work_item.cursor_present"),
            _optional_text(row.get("skip_reason"), "skip_reason"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceRuntimeError:
    code: str
    retryable: bool
    record_kind: str | None
    record_id: str | None
    message: str

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceRuntimeError:
        row = _mapping(value, "Reference source runtime error")
        return cls(
            _text(row.get("code"), "source_error.code"),
            _boolean(row.get("retryable"), "source_error.retryable"),
            _optional_text(row.get("record_kind"), "record_kind"),
            _optional_text(row.get("record_id"), "record_id"),
            _text(row.get("message"), "source_error.message"),
        )


@dataclass(frozen=True, slots=True)
class ReferenceSourceRuntimeStatus:
    source_id: ReferenceSourceIdRead
    provider_id: str | None
    source_kind: ReferenceSourceKind
    configured: bool
    enabled: bool
    paused: bool
    desired_state: ReferenceSourceDesiredState | None
    sync_policy: ReferenceSourceSyncPolicy | None
    scope: ReferenceSourceScope | None
    credential_binding_present: bool | None
    phase: ReferenceSourcePhase
    progress: ReferenceSourceProgress
    work_item: ReferenceSourceWorkItem
    last_attempt_unix_nanos: int | None
    last_success_unix_nanos: int | None
    retry_after_unix_nanos: int | None
    retry_backoff_seconds: int | None
    consecutive_failures: int
    stale: bool
    has_last_known_good: bool
    last_error: ReferenceSourceRuntimeError | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceSourceRuntimeStatus:
        row = _mapping(value, "Reference source runtime status")
        desired_state = row.get("desired_state")
        sync_policy = row.get("sync_policy")
        scope = row.get("scope")
        error = row.get("last_error")
        return cls(
            ReferenceSourceIdRead(_text(row.get("source_id"), "source_id")),
            _optional_text(row.get("provider_id"), "provider_id"),
            ReferenceSourceKind(
                _text(row.get("source_kind", "unknown"), "source_kind")
            ),
            _boolean(row.get("configured", False), "configured"),
            _boolean(row.get("enabled", False), "enabled"),
            _boolean(row.get("paused", False), "paused"),
            (
                None
                if desired_state is None
                else ReferenceSourceDesiredState(_text(desired_state, "desired_state"))
            ),
            (
                None
                if sync_policy is None
                else ReferenceSourceSyncPolicy(_text(sync_policy, "sync_policy"))
            ),
            None if scope is None else ReferenceSourceScope.from_mapping(scope),
            _optional_boolean(
                row.get("credential_binding_present"), "credential_binding_present"
            ),
            ReferenceSourcePhase(_text(row.get("phase"), "source.phase")),
            ReferenceSourceProgress.from_mapping(row.get("progress")),
            ReferenceSourceWorkItem.from_mapping(row.get("work_item", {})),
            _optional_integer(
                row.get("last_attempt_unix_nanos"), "last_attempt_unix_nanos"
            ),
            _optional_integer(
                row.get("last_success_unix_nanos"), "last_success_unix_nanos"
            ),
            _optional_integer(
                row.get("retry_after_unix_nanos"), "retry_after_unix_nanos"
            ),
            _optional_integer(
                row.get("retry_backoff_seconds"), "retry_backoff_seconds"
            ),
            _integer(row.get("consecutive_failures"), "consecutive_failures"),
            _boolean(row.get("stale"), "stale"),
            _boolean(row.get("has_last_known_good"), "has_last_known_good"),
            None if error is None else ReferenceSourceRuntimeError.from_mapping(error),
        )


@dataclass(frozen=True, slots=True)
class ReferenceDiagnostic:
    severity: ReferenceDiagnosticSeverity
    code: str
    message: str
    next_action: str | None
    source_id: ReferenceSourceIdRead | None

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceDiagnostic:
        row = _mapping(value, "Reference diagnostic")
        source_id = _optional_text(row.get("source_id"), "diagnostic.source_id")
        return cls(
            ReferenceDiagnosticSeverity(
                _text(row.get("severity"), "diagnostic.severity")
            ),
            _text(row.get("code"), "diagnostic.code"),
            _text(row.get("message"), "diagnostic.message"),
            _optional_text(row.get("next_action"), "diagnostic.next_action"),
            None if source_id is None else ReferenceSourceIdRead(source_id),
        )


@dataclass(frozen=True, slots=True)
class ReferenceRuntimeStatusResponse:
    status: ReferenceRuntimeState
    app_runtime: ReferenceAppRuntimeStatus
    catalog: ReferenceCatalogRuntimeStatus
    sources: tuple[ReferenceSourceRuntimeStatus, ...]
    coverage: ReferenceCoverageRuntimeStatus
    publication: ReferencePublicationRuntimeStatus
    diagnostics: tuple[ReferenceDiagnostic, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceRuntimeStatusResponse:
        row = _mapping(value, "Reference runtime status response")
        sources = row.get("sources")
        diagnostics = row.get("diagnostics")
        if not isinstance(sources, list):
            raise ValueError("Reference runtime sources must be a list")
        if not isinstance(diagnostics, list):
            raise ValueError("Reference runtime diagnostics must be a list")
        return cls(
            ReferenceRuntimeState(_text(row.get("status"), "runtime.status")),
            ReferenceAppRuntimeStatus.from_mapping(row.get("app_runtime")),
            ReferenceCatalogRuntimeStatus.from_mapping(row.get("catalog")),
            tuple(ReferenceSourceRuntimeStatus.from_mapping(item) for item in sources),
            ReferenceCoverageRuntimeStatus.from_mapping(row.get("coverage", {})),
            ReferencePublicationRuntimeStatus.from_mapping(row.get("publication")),
            tuple(ReferenceDiagnostic.from_mapping(item) for item in diagnostics),
        )

    def to_json_dict(self) -> dict[str, object]:
        """Encode the Rust control contract shape for CLI and Workbench surfaces."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReferenceProviderHealth:
    source_id: ReferenceSourceIdRead
    status: ReferenceProviderStatus
    stale: bool

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceProviderHealth:
        row = _mapping(value, "Reference provider health")
        return cls(
            ReferenceSourceIdRead(_text(row.get("source_id"), "source_id")),
            _provider_status(row.get("status")),
            _boolean(row.get("stale"), "stale"),
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class ReferenceHealthResponse:
    status: ReferenceHealthStatus
    providers: tuple[ReferenceProviderHealth, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ReferenceHealthResponse:
        row = _mapping(value, "Reference health response")
        providers = row.get("providers")
        if not isinstance(providers, list):
            raise ValueError("Reference health providers must be a list")
        return cls(
            _health_status(row.get("status")),
            tuple(ReferenceProviderHealth.from_mapping(item) for item in providers),
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "providers": [provider.to_json_dict() for provider in self.providers],
        }


@dataclass(frozen=True, slots=True)
class ReferenceCatalogCounts:
    exchange_count: int
    asset_count: int
    instrument_count: int
    listing_count: int
    market_count: int
    active_market_count: int


@dataclass(frozen=True, slots=True)
class ReferenceCatalogIntegrity:
    missing_equity_markets: int
    legacy_exchange_market_ids: int
    legacy_exchange_listing_ids: int
    option_listings: int
    option_markets: int


@dataclass(frozen=True, slots=True)
class ReferenceCatalogSnapshot:
    generation: GenerationRead
    event_sequence: SequenceRead
    catalog: ReferenceCatalogCounts
    integrity: ReferenceCatalogIntegrity

    def to_json_dict(self) -> dict[str, object]:
        """Encode the established CLI/JSON shape at the presentation boundary."""

        return {
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "catalog": {
                "exchange_count": self.catalog.exchange_count,
                "asset_count": self.catalog.asset_count,
                "instrument_count": self.catalog.instrument_count,
                "listing_count": self.catalog.listing_count,
                "market_count": self.catalog.market_count,
                "active_market_count": self.catalog.active_market_count,
            },
            "integrity": {
                "missing_equity_markets": self.integrity.missing_equity_markets,
                "legacy_exchange_market_ids": self.integrity.legacy_exchange_market_ids,
                "legacy_exchange_listing_ids": (
                    self.integrity.legacy_exchange_listing_ids
                ),
                "option_listings": self.integrity.option_listings,
                "option_markets": self.integrity.option_markets,
            },
        }


@dataclass(frozen=True, slots=True)
class ReferenceOptionCoverage:
    source_id: ReferenceSourceIdRead
    generation: GenerationRead
    event_sequence: SequenceRead
    underlyings: tuple[InstrumentIdRead, ...]

    def to_json_dict(self) -> dict[str, object]:
        """Encode the established option-coverage JSON shape."""

        return {
            "source_id": self.source_id,
            "generation": self.generation,
            "event_sequence": self.event_sequence,
            "underlyings": list(self.underlyings),
        }


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Reference health {name} must be non-empty text")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Reference health {name} must be a boolean")
    return value


def _optional_boolean(value: object, name: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, name)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Reference {name} must be a non-negative integer")
    return value


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name)


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _health_status(value: object) -> ReferenceHealthStatus:
    if value == "ready":
        return "ready"
    if value == "degraded":
        return "degraded"
    raise ValueError(f"unsupported Reference health status: {value!r}")


def _provider_status(value: object) -> ReferenceProviderStatus:
    if value == "ready":
        return "ready"
    if value == "syncing":
        return "syncing"
    if value == "degraded":
        return "degraded"
    if value == "paused":
        return "paused"
    if value == "disabled":
        return "disabled"
    raise ValueError(f"unsupported Reference provider status: {value!r}")


__all__ = [
    "ReferenceAppPhase",
    "ReferenceAppRuntimeStatus",
    "ReferenceCatalogIntegrityStatus",
    "ReferenceCatalogReadiness",
    "ReferenceCatalogCounts",
    "ReferenceCatalogIntegrity",
    "ReferenceCatalogRuntimeStatus",
    "ReferenceCatalogSnapshot",
    "ReferenceCoverageRuntimeStatus",
    "ReferenceDiagnostic",
    "ReferenceDiagnosticSeverity",
    "ReferenceHealthResponse",
    "ReferenceHealthStatus",
    "ReferenceOptionCoverage",
    "ReferenceProviderHealth",
    "ReferenceProviderStatus",
    "ReferencePublicationRuntimeStatus",
    "ReferenceRuntimeError",
    "ReferenceRuntimeState",
    "ReferenceRuntimeStatusResponse",
    "ReferenceSourceDesiredState",
    "ReferenceSourceKind",
    "ReferenceSourcePhase",
    "ReferenceSourceProgress",
    "ReferenceSourceProgressKind",
    "ReferenceSourceRuntimeError",
    "ReferenceSourceRuntimeStatus",
    "ReferenceSourceScope",
    "ReferenceSourceScopeKind",
    "ReferenceSourceSyncPolicy",
    "ReferenceSourceTickBudget",
    "ReferenceSourceWorkItem",
]
