from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Mapping

from ..application.models import (
    DecisionKind,
    DecisionResult,
    DecisionRuntimeOutput,
    IntentCandidate,
    ReduceTargetQuantity,
    RequireMakerExecution,
    ShortenDeadline,
    TightenLimitPrice,
    TightenMaxSlippage,
    TightenSplitPolicy,
    ToolEvidence,
)


class FixtureDecisionRuntime:
    """Deterministic backtest runtime; never imports a model or MCP client."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fixtures = self._load(path)

    def decide(self, candidate: IntentCandidate) -> DecisionRuntimeOutput:
        key = fixture_key(candidate)
        value = self._fixtures.get(key)
        if value is None:
            raise LookupError(f"Decision fixture does not match candidate: {key}")
        result = value.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("Decision fixture requires a result object")
        return DecisionRuntimeOutput(
            _decision_result(result),
            _tool_evidence(value.get("tool_evidence", ())),
        )

    @staticmethod
    def _load(path: Path) -> Mapping[str, Mapping[str, object]]:
        fixtures: dict[str, Mapping[str, object]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as error:
            raise FileNotFoundError(
                f"Decision fixture does not exist: {path}"
            ) from error
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid Decision fixture JSON at {path}:{line_number}"
                ) from error
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"Decision fixture at {path}:{line_number} must be an object"
                )
            key = value.get("fixture_key")
            result = value.get("result")
            if not isinstance(key, str) or not key or not isinstance(result, Mapping):
                raise ValueError(
                    f"Decision fixture at {path}:{line_number} requires fixture_key/result"
                )
            if key in fixtures:
                raise ValueError(f"Duplicate Decision fixture key: {key}")
            fixtures[key] = value
        return fixtures


def fixture_key(candidate: IntentCandidate) -> str:
    candidate_hash = hashlib.sha256(
        json.dumps(
            _jsonable(candidate.request),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "candidate_hash": candidate_hash,
        "workspace_id": candidate.workspace_id,
        "strategy_id": candidate.strategy_id,
        "launch_id": candidate.launch_id,
        "instance_id": candidate.instance_id,
        "operation": candidate.operation,
        "context_snapshot_hash": candidate.snapshot.context_snapshot_hash,
        "context_watermark": candidate.snapshot.context_watermark,
        "profile_hash": candidate.profile_hash,
        "mode": candidate.snapshot.mode.value,
        "mode_revision": candidate.snapshot.mode_revision,
        "runtime": candidate.runtime,
        "model": candidate.model,
        "tool_profiles": list(candidate.tool_profiles),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decision_result(value: Mapping[str, object]) -> DecisionResult:
    raw_revisions = value.get("revisions", ())
    if not isinstance(raw_revisions, list):
        raise ValueError("Decision fixture revisions must be an array")
    return DecisionResult(
        decision=DecisionKind(str(value.get("decision", ""))),
        confidence_bps=_integer(value.get("confidence_bps"), "confidence_bps"),
        reason_codes=_strings(value.get("reason_codes", ()), "reason_codes"),
        risk_flags=_strings(value.get("risk_flags", ()), "risk_flags"),
        summary=str(value.get("summary", "")),
        revisions=tuple(_revision(item) for item in raw_revisions),
    )


def _tool_evidence(value: object) -> tuple[ToolEvidence, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Decision fixture tool_evidence must be an array")
    evidence: list[ToolEvidence] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Decision fixture tool evidence must be an object")
        evidence.append(
            ToolEvidence(
                tool_name=str(item.get("tool_name", "")),
                argument_hash=_optional_text(item.get("argument_hash")),
                result_hash=_optional_text(item.get("result_hash")),
                status=str(item.get("status", "")),
                observed_at=_optional_text(item.get("observed_at")),
            )
        )
    return tuple(evidence)


def _revision(value: object):
    if not isinstance(value, Mapping):
        raise ValueError("Decision fixture revision must be an object")
    kind = value.get("kind")
    if kind == "reduce_target_quantity":
        return ReduceTargetQuantity(str(value.get("quantity", "")))
    if kind == "tighten_limit_price":
        return TightenLimitPrice(str(value.get("limit_price", "")))
    if kind == "shorten_deadline":
        return ShortenDeadline(_integer(value.get("deadline_unix_nanos"), str(kind)))
    if kind == "tighten_max_slippage":
        return TightenMaxSlippage(_integer(value.get("max_slippage_bps"), str(kind)))
    if kind == "tighten_split_policy":
        return TightenSplitPolicy(
            max_child_quantity=_optional_text(value.get("max_child_quantity")),
            child_count=_optional_integer(value.get("child_count"), str(kind)),
            interval_millis=_optional_integer(value.get("interval_millis"), str(kind)),
        )
    if kind == "require_maker_execution":
        required = value.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("require_maker_execution.required must be boolean")
        return RequireMakerExecution(required)
    raise ValueError(f"Unsupported Decision fixture revision: {kind}")


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Decision fixture cannot encode {type(value).__name__}")


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Decision fixture {name} must be an integer")
    return value


def _optional_integer(value: object, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise ValueError(f"Decision fixture {name} must be an array of strings")
    return tuple(value)


__all__ = ["FixtureDecisionRuntime", "fixture_key"]
