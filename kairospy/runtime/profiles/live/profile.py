from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from kairospy.data.contracts import RunMode
from kairospy.governance.promotion import PromotionDecision, PromotionEvidence, PromotionPolicy
from kairospy.governance.readiness import ReadinessDecision, ReadinessEvidence, require_readiness
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.runtime.kernel import PreparedRun, ProfileResult, RecoveryResult, RunRequest, RunStatus, SubmitResult


@dataclass(frozen=True, slots=True)
class LiveProfile:
    """RunProfile adapter for production-risk live runtime declaration."""

    profile_id: str
    provider: str
    execution_driver: str
    account_binding_hash: str
    data_binding_hash: str
    strategy_hash: str
    config_hash: str
    readiness_evidence: tuple[ReadinessEvidence, ...]
    promotion_evidence: PromotionEvidence
    store: str = "runtime-store"
    recovery_policy: str = "recover-and-reconcile"
    artifact_policy: str = "governance-run-artifact"

    def __post_init__(self) -> None:
        for name in (
            "profile_id", "provider", "execution_driver", "account_binding_hash",
            "data_binding_hash", "strategy_hash", "config_hash", "store",
            "recovery_policy", "artifact_policy",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"live profile requires {name}")
        for item in self.readiness_evidence:
            if item.profile != "live":
                raise ValueError("LiveProfile readiness evidence must use profile='live'")
        if self.promotion_evidence.dataset_hash != self.data_binding_hash:
            raise ValueError("live promotion dataset_hash must match data_binding_hash")
        if self.promotion_evidence.strategy_hash != self.strategy_hash:
            raise ValueError("live promotion strategy_hash must match profile")
        if self.promotion_evidence.config_hash != self.config_hash:
            raise ValueError("live promotion config_hash must match profile")

    @property
    def mode(self) -> RunMode:
        return RunMode.LIVE

    @property
    def required_ports(self) -> tuple[str, ...]:
        return ("market", "reference", "execution", "account")

    @property
    def profile_hash(self) -> str:
        return _hash(self.manifest())

    def manifest(self) -> dict[str, object]:
        return {
            "profile": "live",
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "provider": self.provider,
            "execution_driver": self.execution_driver,
            "account_binding_hash": self.account_binding_hash,
            "data_binding_hash": self.data_binding_hash,
            "strategy_hash": self.strategy_hash,
            "config_hash": self.config_hash,
            "store": self.store,
            "recovery_policy": self.recovery_policy,
            "artifact_policy": self.artifact_policy,
            "required_ports": self.required_ports,
            "readiness_evidence": self.readiness_evidence,
            "promotion_evidence": self.promotion_evidence,
        }

    def require_ready(self) -> tuple[ReadinessDecision, PromotionDecision]:
        readiness = require_readiness("live", self.readiness_evidence)
        promotion = PromotionPolicy().require(self.promotion_evidence)
        return readiness, promotion

    def prepare(self, request: RunRequest) -> PreparedRun:
        _require_request_matches(request, self.mode, self.profile_id, self.data_binding_hash, self.strategy_hash, self.config_hash)
        readiness, promotion = self.require_ready()
        return PreparedRun(
            request,
            self.profile_id,
            self.mode,
            f"live:{self.provider}",
            self.execution_driver,
            self.store,
            _hash({"readiness": readiness, "promotion": promotion}),
            self.recovery_policy,
            self.artifact_policy,
            self.profile_hash,
            {
                "profile": "live",
                "readiness": readiness.status.value,
                "promotion": promotion.approved,
                "account_binding_hash": self.account_binding_hash,
                "required_ports": self.required_ports,
            },
        )

    def market_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def submit(self, commands: Iterable[object]) -> SubmitResult:
        return SubmitResult(
            rejected_command_ids=_command_ids(commands),
            evidence={"reason": "live_execution_gateway_not_bound"},
        )

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        return RecoveryResult(True, False, {"profile": "live", "recovery_policy": self.recovery_policy})

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        return ProfileResult(
            RunStatus.FAILED,
            evidence={"profile": "live", "reason": "live_runtime_gateway_not_bound"},
        )


def live_profile(
    *,
    profile_id: str,
    provider: str,
    execution_driver: str,
    account_binding_hash: str,
    data_binding_hash: str,
    strategy_hash: str,
    config_hash: str,
    readiness_evidence: tuple[ReadinessEvidence, ...],
    promotion_evidence: PromotionEvidence,
    store: str = "runtime-store",
    recovery_policy: str = "recover-and-reconcile",
) -> LiveProfile:
    return LiveProfile(
        profile_id,
        provider,
        execution_driver,
        account_binding_hash,
        data_binding_hash,
        strategy_hash,
        config_hash,
        readiness_evidence,
        promotion_evidence,
        store,
        recovery_policy,
    )


def _require_request_matches(
    request: RunRequest,
    mode: RunMode,
    profile_id: str,
    data_binding_hash: str,
    strategy_hash: str,
    config_hash: str,
) -> None:
    if request.mode is not mode:
        raise ValueError("run request mode must match profile mode")
    if request.profile_id != profile_id:
        raise ValueError("run request profile_id must match profile")
    if request.data_binding_hash != data_binding_hash:
        raise ValueError("run request data_binding_hash must match profile")
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
