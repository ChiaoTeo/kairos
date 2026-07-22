from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable

from kairospy.data.contracts import RunMode
from kairospy.governance.readiness import ReadinessDecision, ReadinessEvidence, require_readiness
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.runtime.kernel import PreparedRun, ProfileResult, RecoveryResult, RunRequest, RunStatus, SubmitResult


class SimulationMarketSource(StrEnum):
    HISTORICAL_REPLAY = "historical-replay"
    RECORDED_REPLAY = "recorded-replay"
    LIVE_CONNECTOR = "live-connector"


class SimulationExecutionBinding(StrEnum):
    LOCAL_SIMULATED = "local-simulated"
    PAPER_ACCOUNT = "paper-account"
    TESTNET = "testnet"


class SimulationClock(StrEnum):
    REPLAY = "replay"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class SimulationProfile:
    """Runtime rehearsal profile with non-production-risk execution."""

    profile_id: str
    mode: RunMode | str
    market_source: SimulationMarketSource | str
    execution_adapter: SimulationExecutionBinding | str
    clock: SimulationClock | str
    dataset_hash: str
    strategy_hash: str
    config_hash: str
    store: str = "runtime-store"
    connector_id: str | None = None
    readiness_evidence: tuple[ReadinessEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("simulation profile requires profile_id")
        object.__setattr__(self, "mode", RunMode(self.mode))
        object.__setattr__(self, "market_source", SimulationMarketSource(self.market_source))
        object.__setattr__(self, "execution_adapter", SimulationExecutionBinding(self.execution_adapter))
        object.__setattr__(self, "clock", SimulationClock(self.clock))
        if self.mode not in {RunMode.HISTORICAL_SIMULATION, RunMode.PAPER_TRADING}:
            raise ValueError("SimulationProfile only supports historical-simulation or paper-trading modes")
        for name in ("dataset_hash", "strategy_hash", "config_hash", "store"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"simulation profile requires {name}")
        if self.market_source in {SimulationMarketSource.HISTORICAL_REPLAY, SimulationMarketSource.RECORDED_REPLAY}:
            if self.clock is not SimulationClock.REPLAY:
                raise ValueError("replay simulation requires replay clock")
        if self.market_source is SimulationMarketSource.LIVE_CONNECTOR and self.clock is not SimulationClock.SYSTEM:
            raise ValueError("live-connector simulation requires system clock")
        if self.execution_adapter in {SimulationExecutionBinding.PAPER_ACCOUNT, SimulationExecutionBinding.TESTNET}:
            if not (self.connector_id or "").strip():
                raise ValueError("paper/testnet simulation requires connector_id")
        for item in self.readiness_evidence:
            if item.profile != "simulation":
                raise ValueError("SimulationProfile readiness evidence must use profile='simulation'")

    @property
    def required_ports(self) -> tuple[str, ...]:
        ports = ["market", "reference", "execution"]
        if self.execution_adapter in {SimulationExecutionBinding.PAPER_ACCOUNT, SimulationExecutionBinding.TESTNET}:
            ports.append("account")
        return tuple(ports)

    @property
    def profile_hash(self) -> str:
        return _hash(self.manifest())

    def manifest(self) -> dict[str, object]:
        return {
            "profile": "simulation",
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "market_source": self.market_source.value,
            "execution_adapter": self.execution_adapter.value,
            "clock": self.clock.value,
            "store": self.store,
            "connector_id": self.connector_id,
            "dataset_hash": self.dataset_hash,
            "strategy_hash": self.strategy_hash,
            "config_hash": self.config_hash,
            "required_ports": self.required_ports,
            "readiness_evidence": self.readiness_evidence,
        }

    def require_ready(self, *, allow_degraded: bool = False) -> ReadinessDecision:
        return require_readiness("simulation", self.readiness_evidence, allow_degraded=allow_degraded)

    def prepare(self, request: RunRequest) -> PreparedRun:
        _require_request_matches(request, self.mode, self.profile_id, self.dataset_hash, self.strategy_hash, self.config_hash)
        decision = self.require_ready()
        return PreparedRun(
            request,
            self.profile_id,
            self.mode,
            self.market_source.value,
            self.execution_adapter.value,
            self.store,
            _hash(decision),
            "runtime-rehearsal-recovery" if self.store != "none" else "none",
            "simulation-artifact+governance-artifact-ref",
            self.profile_hash,
            {
                "profile": "simulation",
                "readiness": decision.status.value,
                "required_ports": self.required_ports,
                "connector_id": self.connector_id,
            },
        )

    def market_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]:
        return ()

    def submit(self, commands: Iterable[object]) -> SubmitResult:
        return SubmitResult(
            rejected_command_ids=_command_ids(commands),
            evidence={"reason": "simulation_execution_adapter_not_bound"},
        )

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        required = self.store != "none"
        return RecoveryResult(required, True, {"profile": "simulation", "store": self.store})

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        return ProfileResult(
            RunStatus.SUCCEEDED,
            evidence={"profile": "simulation", "artifact_policy": "simulation-artifact+governance-artifact-ref"},
            artifact_refs=(f"simulation:{self.profile_id}:{self.profile_hash}",),
        )


def historical_replay_simulation_profile(
    *,
    profile_id: str,
    dataset_hash: str,
    strategy_hash: str,
    config_hash: str,
    readiness_evidence: tuple[ReadinessEvidence, ...] = (),
) -> SimulationProfile:
    return SimulationProfile(
        profile_id=profile_id,
        mode=RunMode.HISTORICAL_SIMULATION,
        market_source=SimulationMarketSource.HISTORICAL_REPLAY,
        execution_adapter=SimulationExecutionBinding.LOCAL_SIMULATED,
        clock=SimulationClock.REPLAY,
        dataset_hash=dataset_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        readiness_evidence=readiness_evidence,
    )


def paper_simulation_profile(
    *,
    provider: str,
    dataset_hash: str,
    strategy_hash: str,
    config_hash: str,
    readiness_evidence: tuple[ReadinessEvidence, ...] = (),
) -> SimulationProfile:
    return SimulationProfile(
        profile_id=f"paper:{provider}",
        mode=RunMode.PAPER_TRADING,
        market_source=SimulationMarketSource.LIVE_CONNECTOR,
        execution_adapter=SimulationExecutionBinding.PAPER_ACCOUNT,
        clock=SimulationClock.SYSTEM,
        dataset_hash=dataset_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        connector_id=provider,
        readiness_evidence=readiness_evidence,
    )


def exchange_testnet_simulation_profile(
    *,
    provider: str,
    dataset_hash: str,
    strategy_hash: str,
    config_hash: str,
    readiness_evidence: tuple[ReadinessEvidence, ...] = (),
) -> SimulationProfile:
    return SimulationProfile(
        profile_id=f"testnet:{provider}",
        mode=RunMode.PAPER_TRADING,
        market_source=SimulationMarketSource.LIVE_CONNECTOR,
        execution_adapter=SimulationExecutionBinding.TESTNET,
        clock=SimulationClock.SYSTEM,
        dataset_hash=dataset_hash,
        strategy_hash=strategy_hash,
        config_hash=config_hash,
        connector_id=provider,
        readiness_evidence=readiness_evidence,
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
