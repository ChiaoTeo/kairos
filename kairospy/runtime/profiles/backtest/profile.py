from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping

from kairospy.data.contracts import RunMode
from kairospy.governance.readiness import ReadinessDecision, ReadinessEvidence, require_readiness
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.runtime.kernel import PreparedRun, ProfileResult, RecoveryResult, RunRequest, RunStatus, SubmitResult


@dataclass(frozen=True, slots=True)
class BacktestProfile:
    """RunProfile adapter for deterministic historical evaluation."""

    profile_id: str
    dataset_hash: str
    strategy_hash: str
    config_hash: str
    reference_hash: str = "none"
    fill_model: str = "deterministic-fill-model"
    store: str = "backtest-artifact"
    readiness_evidence: tuple[ReadinessEvidence, ...] = ()

    def __post_init__(self) -> None:
        for name in ("profile_id", "dataset_hash", "strategy_hash", "config_hash", "reference_hash", "fill_model", "store"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"backtest profile requires {name}")
        for item in self.readiness_evidence:
            if item.profile != "backtest":
                raise ValueError("BacktestProfile readiness evidence must use profile='backtest'")

    @property
    def mode(self) -> RunMode:
        return RunMode.BACKTEST

    @property
    def profile_hash(self) -> str:
        return _hash(self.manifest())

    def manifest(self) -> dict[str, object]:
        return {
            "profile": "backtest",
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "dataset_hash": self.dataset_hash,
            "reference_hash": self.reference_hash,
            "strategy_hash": self.strategy_hash,
            "config_hash": self.config_hash,
            "fill_model": self.fill_model,
            "store": self.store,
            "readiness_evidence": self.readiness_evidence,
        }

    def require_ready(self) -> ReadinessDecision:
        return require_readiness("backtest", self.readiness_evidence)

    def prepare(self, request: RunRequest) -> PreparedRun:
        _require_request_matches(request, self.mode, self.profile_id, self.dataset_hash, self.strategy_hash, self.config_hash)
        decision = self.require_ready()
        return PreparedRun(
            request,
            self.profile_id,
            self.mode,
            f"dataset-release:{self.dataset_hash}",
            self.fill_model,
            self.store,
            _hash(decision),
            "none",
            "backtest-result+governance-artifact-ref",
            self.profile_hash,
            {
                "profile": "backtest",
                "readiness": decision.status.value,
                "dataset_hash": self.dataset_hash,
                "reference_hash": self.reference_hash,
            },
        )

    def market_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def submit(self, commands: Iterable[object]) -> SubmitResult:
        return SubmitResult(
            rejected_command_ids=_command_ids(commands),
            evidence={"reason": "backtest_profile_has_no_live_submit"},
        )

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        return RecoveryResult(False, True, {"profile": "backtest", "recovery_policy": "none"})

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        return ProfileResult(
            RunStatus.SUCCEEDED,
            evidence={"profile": "backtest", "artifact_policy": "backtest-result+governance-artifact-ref"},
            artifact_refs=(f"backtest:{self.profile_id}:{self.profile_hash}",),
        )


def backtest_profile(
    *,
    profile_id: str,
    dataset_hash: str,
    strategy_hash: str,
    config_hash: str,
    readiness_evidence: tuple[ReadinessEvidence, ...],
    reference_hash: str = "none",
    fill_model: str = "deterministic-fill-model",
    store: str = "backtest-artifact",
) -> BacktestProfile:
    return BacktestProfile(
        profile_id,
        dataset_hash,
        strategy_hash,
        config_hash,
        reference_hash,
        fill_model,
        store,
        readiness_evidence,
    )


def _require_request_matches(
    request: RunRequest,
    mode: RunMode,
    profile_id: str,
    dataset_hash: str,
    strategy_hash: str,
    config_hash: str,
) -> None:
    if request.mode is not mode:
        raise ValueError("run request mode must match profile mode")
    if request.profile_id != profile_id:
        raise ValueError("run request profile_id must match profile")
    if request.data_binding_hash != dataset_hash:
        raise ValueError("run request data_binding_hash must match profile dataset_hash")
    if request.strategy_hash != strategy_hash:
        raise ValueError("run request strategy_hash must match profile")
    if request.config_hash != config_hash:
        raise ValueError("run request config_hash must match profile")


def _command_ids(commands: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(getattr(item, "command_id", getattr(item, "client_order_id", item))) for item in commands)


def _hash(value: object) -> str:
    return sha256(json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
