from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping

from kairospy.governance.promotion import PromotionEvidence
from kairospy.governance.readiness import ReadinessEvidence
from kairospy.infrastructure.configuration import ConfigError, KairosProjectConfig
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


def live_runtime_profile_from_config(
    config: KairosProjectConfig,
    *,
    workspace_hash: str,
    strategy_hash: str,
    config_hash: str,
) -> BoundRunProfile:
    return load_live_runtime_binding_config(
        config,
        workspace_hash=workspace_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
    ).bind()


def load_live_runtime_binding_config(
    config: KairosProjectConfig,
    *,
    workspace_hash: str,
    strategy_hash: str,
    config_hash: str,
) -> LiveRuntimeBindingConfig:
    raw = config.get("runtime.live")
    if not isinstance(raw, dict):
        raise ConfigError("run start --mode live requires [runtime.live] configuration")
    if raw.get("enabled") is not True:
        raise ConfigError("run start --mode live requires runtime.live.enabled = true")
    if str(raw.get("data_binding_hash", "")) != workspace_hash:
        raise ConfigError("runtime.live.data_binding_hash must match the current workspace snapshot hash")
    if str(raw.get("strategy_hash", "")) != strategy_hash:
        raise ConfigError("runtime.live.strategy_hash must match the current strategy entrypoint hash")
    if str(raw.get("config_hash", "")) != config_hash:
        raise ConfigError("runtime.live.config_hash must match the current run config hash")

    account_binding_hash = _required(raw, "account_binding_hash")
    binding_id = str(raw.get("binding_id") or f"live-runtime:{_required(raw, 'provider')}")
    recovery = raw.get("recovery")
    if not isinstance(recovery, dict):
        raise ConfigError("runtime.live.recovery table is required")

    readiness = _readiness_evidence(raw.get("readiness"))
    promotion = _promotion_evidence(raw.get("promotion"))
    return LiveRuntimeBindingConfig(
        profile_id=_required(raw, "profile_id"),
        provider=_required(raw, "provider"),
        execution_driver=_required(raw, "execution_driver"),
        account_binding_hash=account_binding_hash,
        data_binding_hash=workspace_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        readiness_evidence=readiness,
        promotion_evidence=promotion,
        binding_id=binding_id,
        recovery_binding_id=str(recovery.get("binding_id") or f"{binding_id}:recovery"),
        recovery_ready=bool(recovery.get("ready")),
        recovery_reason=str(recovery.get("reason") or ("ready" if recovery.get("ready") else "not_ready")),
        store=str(raw.get("store") or "runtime-store"),
        recovery_policy=str(raw.get("recovery_policy") or "recover-and-reconcile"),
    )


def _readiness_evidence(raw: object) -> tuple[ReadinessEvidence, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("runtime.live.readiness must contain at least one readiness evidence table")
    values = []
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError("runtime.live.readiness entries must be tables")
        values.append(ReadinessEvidence(
            profile=str(item.get("profile") or "live"),
            status=str(item.get("status") or ""),
            required_ports=_tuple_str(item.get("required_ports")),
            reason_codes=_tuple_str(item.get("reason_codes")),
            evidence_refs=_mapping_str(item.get("evidence_refs")),
            account_binding=_optional_str(item.get("account_binding")),
            connector_id=_optional_str(item.get("connector_id")),
        ))
    return tuple(values)


def _promotion_evidence(raw: object) -> PromotionEvidence:
    if not isinstance(raw, dict):
        raise ConfigError("runtime.live.promotion table is required")
    recorded_at = raw.get("recorded_at")
    return PromotionEvidence(
        from_stage=_required(raw, "from_stage"),
        to_stage=_required(raw, "to_stage"),
        dataset_hash=_required(raw, "dataset_hash"),
        strategy_hash=_required(raw, "strategy_hash"),
        config_hash=_required(raw, "config_hash"),
        gate_passed=bool(raw.get("gate_passed")),
        evidence_refs=_mapping_str(raw.get("evidence_refs")),
        reason_codes=_tuple_str(raw.get("reason_codes")),
        recorded_at=_datetime(recorded_at),
    )


def _required(raw: Mapping[str, Any], name: str) -> str:
    value = str(raw.get(name) or "")
    if not value.strip():
        raise ConfigError(f"runtime.live.{name} is required")
    return value


def _tuple_str(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("runtime.live list fields must be TOML arrays")
    return tuple(str(item) for item in value)


def _mapping_str(value: object) -> Mapping[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("runtime.live evidence_refs must be a table")
    return {str(key): str(item) for key, item in value.items()}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _datetime(value: object) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ConfigError("runtime.live.promotion.recorded_at must be timezone-aware")
        return value
    text = str(value)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ConfigError("runtime.live.promotion.recorded_at must be timezone-aware")
    return parsed


@dataclass(frozen=True, slots=True)
class _ConfiguredLiveRecovery:
    ready: bool
    reason: str
    service_id: str = "configured-live-recovery"

    def recover(self, at: datetime) -> SimpleNamespace:
        return SimpleNamespace(ready=self.ready, recovered_at=at, reason=self.reason)
