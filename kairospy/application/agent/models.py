from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Mapping, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class AgentMode(StrEnum):
    SHADOW = "shadow"
    GATE = "gate"
    REVISE = "revise"


class AgentContextStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REMOVED = "removed"
    REJECTED = "rejected"


class AgentModeStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"
    ABSTAIN = "abstain"


class DecisionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUBMITTING = "submitting"
    APPROVED = "approved"
    REVISED = "revised"
    REJECTED = "rejected"
    ABSTAINED = "abstained"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    SUBMISSION_INDETERMINATE = "submission_indeterminate"


@dataclass(frozen=True, slots=True)
class AgentContextDocument:
    key: str
    scopes: tuple[str, ...]
    values: Mapping[str, JsonValue]
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    source_event_sequence: int | None = None

    def __post_init__(self) -> None:
        key = self.key.strip()
        if not key or len(key) > 64:
            raise ValueError("Agent context key must contain 1..64 characters")
        if any(character not in _KEY_CHARACTERS for character in key):
            raise ValueError("Agent context key contains unsupported characters")
        scopes = tuple(dict.fromkeys(scope.strip() for scope in self.scopes))
        if not scopes or any(not scope or len(scope) > 96 for scope in scopes):
            raise ValueError("Agent context requires bounded non-empty scopes")
        if len(scopes) > 16:
            raise ValueError("Agent context may contain at most 16 scopes")
        if self.source_event_sequence is not None and self.source_event_sequence < 0:
            raise ValueError("source_event_sequence cannot be negative")
        observed_at = _utc(self.observed_at, "observed_at")
        expires_at = _utc(self.expires_at, "expires_at")
        if (
            observed_at is not None
            and expires_at is not None
            and expires_at <= observed_at
        ):
            raise ValueError("Agent context expires_at must be after observed_at")
        values = _freeze_mapping(self.values, depth=0)
        encoded = json.dumps(
            _jsonable(values), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(encoded) > 16_384:
            raise ValueError("Agent context document exceeds 16384 bytes")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def content_hash(self) -> str:
        payload = {
            "key": self.key,
            "scopes": list(self.scopes),
            "values": _jsonable(self.values),
            "observed_at": _datetime_text(self.observed_at),
            "expires_at": _datetime_text(self.expires_at),
            "source_event_sequence": self.source_event_sequence,
        }
        return hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentContextReceipt:
    key: str
    revision: int
    content_hash: str
    status: AgentContextStatus
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentModeReceipt:
    requested_mode: AgentMode
    effective_mode: AgentMode
    revision: int
    status: AgentModeStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AgentContextSnapshot:
    capability: str
    mode: AgentMode
    mode_revision: int
    context_watermark: int
    context_snapshot_hash: str
    documents: tuple[AgentContextDocument, ...]


@dataclass(frozen=True, slots=True)
class ReduceTargetQuantity:
    quantity: str


@dataclass(frozen=True, slots=True)
class TightenLimitPrice:
    limit_price: str


@dataclass(frozen=True, slots=True)
class ShortenDeadline:
    deadline_unix_nanos: int


@dataclass(frozen=True, slots=True)
class TightenMaxSlippage:
    max_slippage_bps: int


@dataclass(frozen=True, slots=True)
class TightenSplitPolicy:
    max_child_quantity: str | None = None
    child_count: int | None = None
    interval_millis: int | None = None


@dataclass(frozen=True, slots=True)
class RequireMakerExecution:
    required: bool = True


IntentRevision: TypeAlias = (
    ReduceTargetQuantity
    | TightenLimitPrice
    | ShortenDeadline
    | TightenMaxSlippage
    | TightenSplitPolicy
    | RequireMakerExecution
)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: DecisionKind
    confidence_bps: int
    reason_codes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    summary: str
    revisions: tuple[IntentRevision, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", DecisionKind(self.decision))
        if self.confidence_bps < 0 or self.confidence_bps > 10_000:
            raise ValueError("Agent confidence_bps must be between 0 and 10000")
        if len(self.reason_codes) > 32 or len(self.risk_flags) > 32:
            raise ValueError("Agent decision code lists may contain at most 32 items")
        if any(not value.strip() or len(value) > 96 for value in self.reason_codes):
            raise ValueError("Agent reason_codes must contain bounded strings")
        if any(not value.strip() or len(value) > 96 for value in self.risk_flags):
            raise ValueError("Agent risk_flags must contain bounded strings")
        if len(self.summary) > 2048:
            raise ValueError("Agent decision summary exceeds 2048 characters")
        if self.decision is DecisionKind.REVISE and not self.revisions:
            raise ValueError("revise decision requires at least one revision")
        if self.decision is not DecisionKind.REVISE and self.revisions:
            raise ValueError("Only revise decision may contain revisions")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "risk_flags", tuple(self.risk_flags))
        object.__setattr__(self, "revisions", tuple(self.revisions))


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    tool_name: str
    argument_hash: str | None
    result_hash: str | None
    status: str
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_name.strip() or len(self.tool_name) > 192:
            raise ValueError("Tool evidence name must be a bounded string")
        if self.status not in {"completed", "failed", "unavailable"}:
            raise ValueError("Tool evidence status is unsupported")
        for name in ("argument_hash", "result_hash"):
            value = getattr(self, name)
            if value is not None and (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"Tool evidence {name} must be a SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class DecisionRuntimeOutput:
    result: DecisionResult
    tool_evidence: tuple[ToolEvidence, ...] = ()

    def __post_init__(self) -> None:
        if len(self.tool_evidence) > 64:
            raise ValueError("Decision runtime returned too many tool evidence records")
        object.__setattr__(self, "tool_evidence", tuple(self.tool_evidence))


@dataclass(frozen=True, slots=True)
class IntentCandidate:
    decision_id: str
    request_id: str
    intent_id: str
    strategy_id: str
    launch_id: str
    instance_id: str
    operation: str
    request: object
    exposure_effect: str
    profile_hash: str
    snapshot: AgentContextSnapshot
    submitted_at: datetime
    deadline: datetime
    runtime: str = "unknown"
    model: str | None = None
    tool_profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "request_id",
            "intent_id",
            "strategy_id",
            "launch_id",
            "instance_id",
            "operation",
            "profile_hash",
            "runtime",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"Agent candidate {name} is required")
        if self.exposure_effect not in {"increase", "reduce", "neutral", "unknown"}:
            raise ValueError("Agent candidate exposure_effect is invalid")
        submitted = _utc(self.submitted_at, "submitted_at")
        deadline = _utc(self.deadline, "deadline")
        assert submitted is not None and deadline is not None
        if deadline <= submitted:
            raise ValueError("Agent candidate deadline must be after submitted_at")
        object.__setattr__(self, "submitted_at", submitted)
        object.__setattr__(self, "deadline", deadline)
        if self.model is not None and not self.model.strip():
            raise ValueError("Agent candidate model cannot be blank")
        if len(self.tool_profiles) > 32 or any(
            not value.strip() or len(value) > 192 for value in self.tool_profiles
        ):
            raise ValueError("Agent candidate tool_profiles must contain bounded IDs")
        object.__setattr__(self, "tool_profiles", tuple(self.tool_profiles))


@dataclass(frozen=True, slots=True)
class DecisionReceipt:
    decision_id: str
    request_id: str
    intent_id: str | None
    status: DecisionStatus
    final_submission_status: str | None = None
    delivery_certainty: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionAgentHealth:
    enabled: bool
    required: bool
    state: str
    mode: AgentMode
    mode_revision: int
    context_watermark: int
    context_documents: int
    queue_depth: int = 0
    queue_capacity: int = 0
    in_flight: int = 0
    last_success_at: datetime | None = None
    last_failure: str | None = None
    runtime: str | None = None
    model: str | None = None
    mcp_servers: int = 0
    store_ready: bool = False
    rolling_error_rate: float = 0.0
    latency_p50_millis: float | None = None
    latency_p95_millis: float | None = None
    latency_p99_millis: float | None = None


_KEY_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_SECRET_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


def _freeze_mapping(
    value: Mapping[str, JsonValue], *, depth: int
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError("Agent context values must be an object")
    if depth > 4:
        raise ValueError("Agent context exceeds maximum nesting depth")
    if len(value) > 64:
        raise ValueError("Agent context object may contain at most 64 fields")
    result: dict[str, JsonValue] = {}
    for raw_key, item in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip() or len(raw_key) > 96:
            raise ValueError("Agent context field names must be bounded strings")
        key = raw_key.strip()
        lowered = key.lower()
        if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
            raise ValueError(f"Agent context field is credential-like: {key}")
        result[key] = _freeze_value(item, depth=depth + 1)
    return MappingProxyType(result)


def _freeze_value(value: object, *, depth: int) -> JsonValue:
    if isinstance(value, Mapping):
        return _freeze_mapping(value, depth=depth)
    if isinstance(value, (list, tuple)):
        if depth > 4:
            raise ValueError("Agent context exceeds maximum nesting depth")
        if len(value) > 128:
            raise ValueError("Agent context array may contain at most 128 items")
        return tuple(_freeze_value(item, depth=depth + 1) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 4096:
            raise ValueError("Agent context string exceeds 4096 characters")
        return value
    raise ValueError(
        f"Agent context contains unsupported value: {type(value).__name__}"
    )


def _jsonable(value: JsonValue | Mapping[str, JsonValue]) -> object:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _utc(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"Agent context {name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = [
    "AgentContextDocument",
    "AgentContextReceipt",
    "AgentContextSnapshot",
    "AgentContextStatus",
    "AgentMode",
    "AgentModeReceipt",
    "AgentModeStatus",
    "DecisionKind",
    "DecisionReceipt",
    "DecisionResult",
    "DecisionRuntimeOutput",
    "DecisionStatus",
    "DecisionAgentHealth",
    "JsonValue",
    "IntentCandidate",
    "IntentRevision",
    "ReduceTargetQuantity",
    "RequireMakerExecution",
    "ShortenDeadline",
    "TightenLimitPrice",
    "TightenMaxSlippage",
    "TightenSplitPolicy",
    "ToolEvidence",
]
