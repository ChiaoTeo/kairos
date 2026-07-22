from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace
from typing import Any, Mapping

from kairospy.governance.promotion import PromotionEvidence
from kairospy.governance.readiness import ReadinessEvidence
from kairospy.infrastructure.configuration import ConfigError
from kairospy.runtime.kernel import BoundRunProfile, RuntimeRecoveryBinding
from kairospy.runtime.profiles.live import LiveProfile, live_profile


@dataclass(frozen=True, slots=True)
class LiveRuntimeBindingConfig:
    """Project-level live runtime evidence, not a connector capability model."""

    profile_id: str
    provider: str
    execution_driver: str
    account_binding_hash: str
    data_binding_hash: str
    strategy_hash: str
    config_hash: str
    readiness_evidence: tuple[ReadinessEvidence, ...]
    promotion_evidence: PromotionEvidence
    binding_id: str
    recovery_binding_id: str
    recovery_ready: bool
    recovery_reason: str
    store: str = "runtime-store"
    recovery_policy: str = "recover-and-reconcile"

    def to_live_profile(self) -> LiveProfile:
        return live_profile(
            profile_id=self.profile_id,
            provider=self.provider,
            execution_driver=self.execution_driver,
            account_binding_hash=self.account_binding_hash,
            data_binding_hash=self.data_binding_hash,
            strategy_hash=self.strategy_hash,
            config_hash=self.config_hash,
            readiness_evidence=self.readiness_evidence,
            promotion_evidence=self.promotion_evidence,
            store=self.store,
            recovery_policy=self.recovery_policy,
        )

    def bind(self) -> BoundRunProfile:
        return BoundRunProfile(
            self.to_live_profile(),
            self.binding_id,
            recovery_handler=self.runtime_recovery_handler(),
        )

    def runtime_recovery_service(self) -> object:
        return _ConfiguredLiveRecovery(self.recovery_ready, self.recovery_reason)

    def runtime_recovery_handler(self) -> RuntimeRecoveryBinding:
        return RuntimeRecoveryBinding(
            self.runtime_recovery_service(),
            self.recovery_binding_id,
        )


def live_runtime_binding_config_from_run_config(
    run_config: object,
    *,
    workspace_hash: str,
    strategy_hash: str,
    config_hash: str,
) -> LiveRuntimeBindingConfig:
    run = _run_config_table(run_config, "run")
    live = _run_config_table(run_config, "live")
    bindings = _run_config_table(run_config, "bindings")
    guards = _run_config_table(run_config, "guards", required=False)
    evidence = _run_config_table(run_config, "evidence", required=False)
    provider = _required(live, "provider")
    execution_driver = str(live.get("execution_driver") or f"{provider}-live")
    account_binding = _required(bindings, "account")
    readiness_ref = str(evidence.get("readiness") or "")
    promotion_ref = str(evidence.get("promotion") or "")
    if guards.get("require_readiness") is True and not readiness_ref:
        raise ConfigError("RunConfig evidence.readiness is required when guards.require_readiness = true")
    if guards.get("require_promotion") is True and not promotion_ref:
        raise ConfigError("RunConfig evidence.promotion is required when guards.require_promotion = true")
    binding_id = str(live.get("binding_id") or f"live-runtime:{provider}:{run.get('name') or 'run'}")
    account_binding_hash = _stable_hash({"account": account_binding})
    readiness = (ReadinessEvidence(
        profile="live",
        status="pass",
        required_ports=("market", "reference", "execution", "account"),
        evidence_refs={"readiness": readiness_ref} if readiness_ref else {},
        account_binding=account_binding_hash,
        connector_id=provider,
    ),)
    promotion = PromotionEvidence(
        from_stage=str(evidence.get("from_stage") or "PAPER_APPROVED"),
        to_stage=str(evidence.get("to_stage") or "LIVE_LIMITED"),
        dataset_hash=workspace_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        gate_passed=True,
        evidence_refs={
            key: value
            for key, value in {
                "readiness": readiness_ref,
                "promotion": promotion_ref,
            }.items()
            if value
        },
    )
    return LiveRuntimeBindingConfig(
        profile_id=str(live.get("profile_id") or "profile:live"),
        provider=provider,
        execution_driver=execution_driver,
        account_binding_hash=account_binding_hash,
        data_binding_hash=workspace_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        readiness_evidence=readiness,
        promotion_evidence=promotion,
        binding_id=binding_id,
        recovery_binding_id=str(live.get("recovery_binding_id") or f"{binding_id}:recovery"),
        recovery_ready=bool(live.get("recovery_ready", True)),
        recovery_reason=str(live.get("recovery_reason") or "RunConfig recovery gate satisfied"),
        store=str(live.get("store") or "runtime-store"),
        recovery_policy=str(live.get("recovery_policy") or "recover-and-reconcile"),
    )


def _required(raw: Mapping[str, Any], name: str) -> str:
    value = str(raw.get(name) or "")
    if not value.strip():
        raise ConfigError(f"RunConfig field is required: {name}")
    return value


def _run_config_table(run_config: object, name: str, *, required: bool = True) -> dict[str, Any]:
    getter = getattr(run_config, "get", None)
    raw = getter(name, {}) if callable(getter) else {}
    if raw in (None, {}) and not required:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"RunConfig [{name}] must be a table")
    if required and not raw:
        raise ConfigError(f"RunConfig [{name}] table is required")
    return dict(raw)


def _stable_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _ConfiguredLiveRecovery:
    ready: bool
    reason: str
    service_id: str = "configured-live-recovery"

    def recover(self, at: datetime) -> SimpleNamespace:
        return SimpleNamespace(ready=self.ready, recovered_at=at, reason=self.reason)
