from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Callable, Iterable, Mapping, Protocol, TYPE_CHECKING

from kairospy.data.contracts import RunMode
from kairospy.strategy.contracts import EconomicIntent
from kairospy.execution.intent_coordinator import IntentCoordinator
from kairospy.execution.intent_status import IntentExecutionTracker
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.strategy.protocols import Context, StrategyDecision
from kairospy.strategy.runtime import GovernedStrategyRuntime
from kairospy.strategy.views import BudgetView, FeatureView

if TYPE_CHECKING:
    from kairospy.analytics.features.runtime import FactorRuntime, FactorSnapshot
    from kairospy.market.canonical import CanonicalEventEnvelope
    from kairospy.market.snapshots import MarketSnapshot
    from kairospy.market.stream import EventSource


class RunStatus(StrEnum):
    REQUESTED = "requested"
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunRequest:
    run_id: str
    mode: RunMode | str
    profile_id: str
    workspace_hash: str
    data_binding_hash: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    config_hash: str
    requested_at: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", RunMode(self.mode))
        for name in (
            "run_id", "profile_id", "workspace_hash", "data_binding_hash",
            "strategy_id", "strategy_version", "strategy_hash", "config_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"run request requires {name}")
        if self.requested_at.tzinfo is None:
            raise ValueError("run request requested_at must be timezone-aware")

    @property
    def request_hash(self) -> str:
        return _hash(self)

    def manifest(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "profile_id": self.profile_id,
            "workspace_hash": self.workspace_hash,
            "data_binding_hash": self.data_binding_hash,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_hash": self.strategy_hash,
            "config_hash": self.config_hash,
            "requested_at": self.requested_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PreparedRun:
    request: RunRequest
    profile_id: str
    mode: RunMode | str
    market_source: str
    execution_driver: str
    store_policy: str
    readiness_hash: str
    recovery_policy: str
    artifact_policy: str
    profile_hash: str
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", RunMode(self.mode))
        if self.mode is not self.request.mode:
            raise ValueError("prepared run mode must match request mode")
        if self.profile_id != self.request.profile_id:
            raise ValueError("prepared run profile_id must match request profile_id")
        for name in (
            "market_source", "execution_driver", "store_policy", "readiness_hash",
            "recovery_policy", "artifact_policy", "profile_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"prepared run requires {name}")

    @property
    def prepared_hash(self) -> str:
        return _hash(self)

    def manifest(self) -> dict[str, object]:
        return {
            "request_hash": self.request.request_hash,
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "market_source": self.market_source,
            "execution_driver": self.execution_driver,
            "store_policy": self.store_policy,
            "readiness_hash": self.readiness_hash,
            "recovery_policy": self.recovery_policy,
            "artifact_policy": self.artifact_policy,
            "profile_hash": self.profile_hash,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class SubmitResult:
    accepted_command_ids: tuple[str, ...] = ()
    rejected_command_ids: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def submit_hash(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    required: bool
    recovered: bool
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def recovery_hash(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class ProfileResult:
    status: RunStatus | str
    evidence: Mapping[str, object] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    artifact_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", RunStatus(self.status))

    @property
    def profile_result_hash(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class RunArtifactLink:
    artifact_hash: str
    artifact_refs: tuple[str, ...] = ()
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.artifact_hash.strip():
            raise ValueError("run artifact link requires artifact_hash")
        if any(not str(item).strip() for item in self.artifact_refs):
            raise ValueError("run artifact refs must not contain empty values")

    @property
    def link_hash(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    mode: RunMode | str
    profile_id: str
    status: RunStatus | str
    request_hash: str
    prepared_hash: str
    strategy_run_hash: str
    recovery_hash: str
    profile_result_hash: str
    evidence_hash: str
    artifact_hash: str | None = None
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", RunMode(self.mode))
        object.__setattr__(self, "status", RunStatus(self.status))

    @property
    def result_hash(self) -> str:
        return _hash(self)

    def manifest(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "profile_id": self.profile_id,
            "status": self.status.value,
            "request_hash": self.request_hash,
            "prepared_hash": self.prepared_hash,
            "strategy_run_hash": self.strategy_run_hash,
            "recovery_hash": self.recovery_hash,
            "profile_result_hash": self.profile_result_hash,
            "evidence_hash": self.evidence_hash,
            "artifact_hash": self.artifact_hash,
            "artifact_refs": self.artifact_refs,
        }


class RunProfile(Protocol):
    @property
    def profile_id(self) -> str: ...

    @property
    def mode(self) -> RunMode: ...

    @property
    def profile_hash(self) -> str: ...

    def manifest(self) -> Mapping[str, object]: ...

    def prepare(self, request: RunRequest) -> PreparedRun: ...

    def market_events(self, prepared: PreparedRun) -> Iterable[object]: ...

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]: ...

    def submit(self, commands: Iterable[object]) -> SubmitResult: ...

    def recover(self, prepared: PreparedRun) -> RecoveryResult: ...

    def finalize(self, prepared: PreparedRun) -> ProfileResult: ...


class RunArtifactWriter(Protocol):
    def __call__(
        self,
        prepared: PreparedRun,
        strategy_result: "StrategyRunResult",
        profile_result: ProfileResult,
    ) -> RunArtifactLink: ...


RunEventProvider = Callable[[PreparedRun], Iterable[object]]
RunCommandSubmitter = Callable[[Iterable[object]], SubmitResult]
RunRecoveryHandler = Callable[[PreparedRun], RecoveryResult]


@dataclass(frozen=True, slots=True)
class IterableRunEventProvider:
    values: Iterable[object]
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("run event provider requires binding_id")
        object.__setattr__(self, "values", tuple(self.values))

    def __call__(self, prepared: PreparedRun) -> Iterable[object]:
        return self.values


@dataclass(frozen=True, slots=True)
class RunCommandSubmitterBinding:
    submitter: Callable[[object], object]
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("run command submitter requires binding_id")
        if not callable(self.submitter):
            raise ValueError("run command submitter requires callable submitter")

    def __call__(self, commands: Iterable[object]) -> SubmitResult:
        accepted: list[str] = []
        rejected: list[str] = []
        errors: list[tuple[str, str]] = []
        for command in commands:
            command_id = _command_id(command)
            try:
                self.submitter(command)
            except Exception as exc:
                rejected.append(command_id)
                errors.append((command_id, type(exc).__name__))
            else:
                accepted.append(command_id)
        return SubmitResult(
            tuple(accepted),
            tuple(rejected),
            {"binding_id": self.binding_id, "errors": tuple(errors)},
        )


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryBinding:
    recovery: object
    binding_id: str

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("runtime recovery binding requires binding_id")
        if not hasattr(self.recovery, "recover"):
            raise ValueError("runtime recovery binding requires recover(at)")

    def __call__(self, prepared: PreparedRun) -> RecoveryResult:
        result = self.recovery.recover(prepared.request.requested_at)
        ready = bool(getattr(result, "ready", False))
        return RecoveryResult(
            True,
            ready,
            {
                "binding_id": self.binding_id,
                "recovered_at": str(getattr(result, "recovered_at", prepared.request.requested_at)),
                "reason": str(getattr(result, "reason", "")),
            },
        )


@dataclass(frozen=True, slots=True)
class BoundRunProfile:
    profile: RunProfile
    binding_id: str
    market_event_provider: RunEventProvider | None = None
    execution_event_provider: RunEventProvider | None = None
    command_submitter: RunCommandSubmitter | None = None
    recovery_handler: RunRecoveryHandler | None = None

    def __post_init__(self) -> None:
        if not self.binding_id.strip():
            raise ValueError("bound run profile requires binding_id")

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def mode(self) -> RunMode:
        return self.profile.mode

    @property
    def profile_hash(self) -> str:
        return self.profile.profile_hash

    @property
    def binding_hash(self) -> str:
        return _hash(self.manifest()["runtime_bindings"])

    def manifest(self) -> Mapping[str, object]:
        return {
            **dict(self.profile.manifest()),
            "runtime_bindings": {
                "binding_id": self.binding_id,
                "market_event_provider": _binding_name(self.market_event_provider),
                "execution_event_provider": _binding_name(self.execution_event_provider),
                "command_submitter": _binding_name(self.command_submitter),
                "recovery_handler": _binding_name(self.recovery_handler),
            },
        }

    def prepare(self, request: RunRequest) -> PreparedRun:
        prepared = self.profile.prepare(request)
        return replace(
            prepared,
            evidence={
                **dict(prepared.evidence),
                "runtime_bindings": self.manifest()["runtime_bindings"],
                "runtime_binding_hash": self.binding_hash,
            },
        )

    def market_events(self, prepared: PreparedRun) -> Iterable[object]:
        if self.market_event_provider is None:
            return self.profile.market_events(prepared)
        return self.market_event_provider(prepared)

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]:
        if self.execution_event_provider is None:
            return self.profile.execution_events(prepared)
        return self.execution_event_provider(prepared)

    def submit(self, commands: Iterable[object]) -> SubmitResult:
        if self.command_submitter is None:
            return self.profile.submit(commands)
        return self.command_submitter(commands)

    def recover(self, prepared: PreparedRun) -> RecoveryResult:
        if self.recovery_handler is None:
            return self.profile.recover(prepared)
        return self.recovery_handler(prepared)

    def finalize(self, prepared: PreparedRun) -> ProfileResult:
        return self.profile.finalize(prepared)


class RunKernel:
    """Thin run contract boundary around profile-specific runtime behavior."""

    def __init__(self, profile: RunProfile) -> None:
        self.profile = profile

    def prepare(self, request: RunRequest) -> PreparedRun:
        self._validate_request(request)
        prepared = self.profile.prepare(request)
        if prepared.profile_hash != self.profile.profile_hash:
            raise ValueError("prepared run profile_hash must match profile")
        return prepared

    def market_events(self, prepared: PreparedRun) -> Iterable[object]:
        return self.profile.market_events(prepared)

    def execution_events(self, prepared: PreparedRun) -> Iterable[object]:
        return self.profile.execution_events(prepared)

    def submit(self, commands: Iterable[object]) -> SubmitResult:
        return self.profile.submit(commands)

    def run(
        self,
        request: RunRequest,
        strategy_runner: Callable[[PreparedRun], StrategyRunResult],
        *,
        artifact_writer: RunArtifactWriter | None = None,
    ) -> RunResult:
        if not callable(strategy_runner):
            raise ValueError("run kernel requires a strategy runner")
        prepared = self.prepare(request)
        recovery = self.profile.recover(prepared)
        if recovery.required and not recovery.recovered:
            profile_result = ProfileResult(
                RunStatus.FAILED,
                evidence={"reason": "recovery_failed", "recovery_hash": recovery.recovery_hash},
            )
            evidence = {
                "request_hash": request.request_hash,
                "prepared_hash": prepared.prepared_hash,
                "profile_hash": self.profile.profile_hash,
                "strategy_run_audit_hash": "not-run",
                "recovery_hash": recovery.recovery_hash,
                "profile_result_hash": profile_result.profile_result_hash,
            }
            return RunResult(
                request.run_id,
                request.mode,
                request.profile_id,
                RunStatus.FAILED,
                request.request_hash,
                prepared.prepared_hash,
                "not-run",
                recovery.recovery_hash,
                profile_result.profile_result_hash,
                _hash(evidence),
            )
        strategy_result = strategy_runner(prepared)
        profile_result = self.profile.finalize(prepared)
        status = RunStatus.SUCCEEDED if profile_result.status is RunStatus.SUCCEEDED else profile_result.status
        artifact_hash = profile_result.artifact_hash or _artifact_refs_hash(profile_result.artifact_refs)
        artifact_refs = profile_result.artifact_refs
        artifact_link_hash = None
        if artifact_writer is not None:
            artifact_link = artifact_writer(prepared, strategy_result, profile_result)
            artifact_hash = artifact_link.artifact_hash
            artifact_refs = tuple(dict.fromkeys((*artifact_refs, *artifact_link.artifact_refs)))
            artifact_link_hash = artifact_link.link_hash
        evidence = {
            "request_hash": request.request_hash,
            "prepared_hash": prepared.prepared_hash,
            "profile_hash": self.profile.profile_hash,
            "strategy_run_audit_hash": strategy_result.audit_hash,
            "context_hash": strategy_result.context_hash,
            "context_view_hashes": dict(strategy_result.context_view_hashes),
            "recovery_hash": recovery.recovery_hash,
            "profile_result_hash": profile_result.profile_result_hash,
            "artifact_hash": artifact_hash,
            "artifact_link_hash": artifact_link_hash,
        }
        return RunResult(
            request.run_id,
            request.mode,
            request.profile_id,
            status,
            request.request_hash,
            prepared.prepared_hash,
            strategy_result.audit_hash,
            recovery.recovery_hash,
            profile_result.profile_result_hash,
            _hash(evidence),
            artifact_hash,
            artifact_refs,
        )

    def _validate_request(self, request: RunRequest) -> None:
        if request.profile_id != self.profile.profile_id:
            raise ValueError("run request profile_id must match profile")
        if request.mode is not self.profile.mode:
            raise ValueError("run request mode must match profile mode")
        manifest = self.profile.manifest()
        for name in ("profile_id", "mode"):
            if name not in manifest:
                raise ValueError(f"run profile manifest missing {name}")


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    event_message_ids: tuple[str, ...]
    factor_snapshots: tuple[FactorSnapshot, ...]
    decisions: tuple[StrategyDecision, ...]
    economic_intents: tuple[EconomicIntent, ...]
    factor_hash: str
    decision_hash: str
    intent_hash: str
    audit_hash: str
    context_view_hashes: Mapping[str, str] = field(default_factory=dict)
    context_hash: str = ""


class StrategyRunHooks(Protocol):
    def before_decision(
        self, event: CanonicalEventEnvelope, market: MarketSnapshot, factor: FactorSnapshot,
    ) -> None: ...

    def on_intent(
        self, event: CanonicalEventEnvelope, market: MarketSnapshot, factor: FactorSnapshot,
        intent: EconomicIntent,
    ) -> None: ...

    def on_end(self, context: Context) -> None: ...


class CanonicalBarMarketProjection:
    def __init__(self) -> None:
        from kairospy.market.projections import CanonicalBarSeriesProjection

        self._bars = CanonicalBarSeriesProjection()
        self._sequence = 0

    def apply(self, event: CanonicalEventEnvelope) -> MarketSnapshot | None:
        bar = self._bars.apply(event)
        if bar is None:
            return None
        from kairospy.market.snapshots import MarketSnapshot

        self._sequence += 1
        return MarketSnapshot(
            bar.end, (), ((bar.instrument_id, bar.close),), sequence=self._sequence,
            available_instruments=(bar.instrument_id,),
            available_time=event.available_time,
            freshness_seconds=Decimal(str((event.receive_time - event.event_time).total_seconds())),
            data_binding=event.source_instance,
            event_window=(bar.start, bar.end),
        )


class GovernedStrategyRunLoop:
    """Shared deterministic decision loop used before any execution driver boundary."""

    def __init__(
        self,
        source: EventSource[CanonicalEventEnvelope],
        factor_runtime: FactorRuntime,
        strategy_runtime: GovernedStrategyRuntime,
        context_factory: Callable[[MarketSnapshot], Context],
        *,
        approved_capital: Decimal,
        hooks: StrategyRunHooks | None = None,
        intent_tracker: IntentExecutionTracker | None = None,
        intent_coordinator: IntentCoordinator | None = None,
    ) -> None:
        if approved_capital <= 0:
            raise ValueError("strategy run requires positive approved capital")
        self.source = source
        self.factor_runtime = factor_runtime
        self.strategy_runtime = strategy_runtime
        self.context_factory = context_factory
        self.approved_capital = approved_capital
        self.hooks = hooks
        self.intent_coordinator = intent_coordinator or IntentCoordinator(
            strategy_runtime, intent_tracker or IntentExecutionTracker(),
        )

    async def run(self) -> StrategyRunResult:
        market_projection = CanonicalBarMarketProjection()
        event_ids: list[str] = []
        factors: list[FactorSnapshot] = []
        intents: list[EconomicIntent] = []
        last_context: Context | None = None
        started = False
        async for event in self.source.events():
            market = market_projection.apply(event)
            factor = self.factor_runtime.update(event)
            if market is None or factor is None:
                continue
            if self.hooks is not None:
                self.hooks.before_decision(event, market, factor)
            event_ids.append(str(event.message_id))
            factors.append(factor)
            base = self.context_factory(market)
            context = replace(
                base,
                features=FeatureView.from_snapshots(existing=base.features.values, factor_snapshots=(factor,)),
                intents=self.intent_coordinator.intent_view(),
                budget=BudgetView.from_evidence(
                    as_of=event.available_time,
                    approved_capital=self.approved_capital,
                    remaining_capital=base.budget.remaining_capital,
                    risk_state=base.budget.risk_state,
                    strategy_positions=base.budget.strategy_positions,
                    reduce_only=base.budget.reduce_only,
                    blocked_reason=base.budget.blocked_reason,
                ),
            )
            if not started:
                if intent := self.intent_coordinator.publish(
                    self.strategy_runtime.intents_on_start(context),
                    context,
                ):
                    intents.append(intent)
                started = True
            if intent := self.intent_coordinator.publish(
                self.strategy_runtime.intents_on_market(context),
                context,
            ):
                intents.append(intent)
                if self.hooks is not None:
                    self.hooks.on_intent(event, market, factor, intent)
            last_context = context
        if last_context is not None:
            if intent := self.intent_coordinator.publish(
                self.strategy_runtime.intents_on_end(last_context),
                last_context,
            ):
                intents.append(intent)
            if self.hooks is not None:
                self.hooks.on_end(last_context)
        decisions = tuple(self.strategy_runtime.strategy.decisions)
        factor_hash = _hash(factors)
        decision_hash = _hash(decisions)
        intent_hash = _hash(intents)
        audit_hash = _hash({
            "events": event_ids,
            "factor_hash": factor_hash,
            "decision_hash": decision_hash,
            "intent_hash": intent_hash,
            "context_hash": last_context.context_hash if last_context is not None else "",
        })
        return StrategyRunResult(
            tuple(event_ids), tuple(factors), decisions, tuple(intents), factor_hash,
            decision_hash, intent_hash, audit_hash,
            dict(last_context.view_hashes) if last_context is not None else {},
            last_context.context_hash if last_context is not None else "",
        )


def _hash(value: object) -> str:
    encoded = json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    return sha256(encoded).hexdigest()


def _artifact_refs_hash(refs: tuple[str, ...]) -> str | None:
    return _hash(refs) if refs else None


def _binding_name(value: object | None) -> str:
    if value is None:
        return "unbound"
    return str(getattr(value, "binding_id", type(value).__name__))


def _command_id(command: object) -> str:
    return str(getattr(command, "command_id", getattr(command, "client_order_id", command)))
