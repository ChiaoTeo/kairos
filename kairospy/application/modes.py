from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Awaitable, Callable, Mapping

from kairospy.data.contracts import RunMode
from kairospy.market_data.subscriptions import CapturePolicy
from .service_supervisor import ManagedServiceSpec, ServiceCriticality


@dataclass(frozen=True, slots=True)
class RunModeComposition:
    """Auditable declaration of the replaceable parts of one strategy run."""

    mode: RunMode
    event_source: str
    clock: str
    execution_driver: str
    persistence: str
    safety_policy: str
    capture_policy: CapturePolicy

    def __post_init__(self) -> None:
        for name in ("event_source", "clock", "execution_driver", "persistence", "safety_policy"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"run-mode composition {name} cannot be empty")
        live = self.mode in {RunMode.PAPER_TRADING, RunMode.LIVE}
        if live and self.capture_policy is CapturePolicy.NONE:
            raise ValueError("live modes require canonical capture")
        if live and self.persistence == "none":
            raise ValueError("live modes require durable persistence")
        if self.mode is RunMode.BACKTEST and self.clock != "replay":
            raise ValueError("backtest mode requires replay clock")
        if self.mode is RunMode.LIVE and self.execution_driver in {"none", "simulated"}:
            raise ValueError("live mode requires a real execution driver")

    @property
    def composition_hash(self) -> str:
        material = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def manifest(self) -> dict[str, str]:
        return {
            "mode": self.mode.value,
            "event_source": self.event_source,
            "clock": self.clock,
            "execution_driver": self.execution_driver,
            "persistence": self.persistence,
            "safety_policy": self.safety_policy,
            "capture_policy": self.capture_policy.value,
        }

    def bind(self,*,event_source:object,clock:object,execution_driver:object,persistence:object,
             safety_policy:object,runner:Callable[[],object])->"ExecutableRunComposition":
        return ExecutableRunComposition(self,(
            ComponentBinding(self.event_source,event_source),ComponentBinding(self.clock,clock),
            ComponentBinding(self.execution_driver,execution_driver),ComponentBinding(self.persistence,persistence),
            ComponentBinding(self.safety_policy,safety_policy)),runner)


@dataclass(frozen=True, slots=True)
class RuntimeFeedServicePlan:
    name: str
    dataset: str
    live_view_id: str
    event_source_contract: str
    channel_contract: str
    capture_policy: CapturePolicy

    def __post_init__(self) -> None:
        required = {
            "name": self.name,
            "dataset": self.dataset,
            "live_view_id": self.live_view_id,
            "event_source_contract": self.event_source_contract,
            "channel_contract": self.channel_contract,
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if missing:
            raise ValueError(f"runtime feed service plan missing {', '.join(missing)}")

    @property
    def service_id(self) -> str:
        return _runtime_feed_service_id(self)

    def manifest(self) -> dict[str, str]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "dataset": self.dataset,
            "live_view_id": self.live_view_id,
            "event_source_contract": self.event_source_contract,
            "channel_contract": self.channel_contract,
            "capture_policy": self.capture_policy.value,
        }


@dataclass(frozen=True, slots=True)
class RuntimeFeedPlan:
    mode: RunMode
    services: tuple[RuntimeFeedServicePlan, ...]

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.PAPER_TRADING, RunMode.LIVE}:
            raise ValueError("runtime feed plan is only valid for paper/live modes")
        if not self.services:
            raise ValueError("paper/live runtime feed plan requires at least one feed binding")
        service_ids = [item.service_id for item in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("runtime feed plan service ids must be unique")

    @property
    def plan_hash(self) -> str:
        material = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def manifest(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "services": [item.manifest() for item in self.services],
        }

    @property
    def service_bundle_hash(self) -> str:
        material = json.dumps(self.service_bundle_manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def service_bundle_manifest(self) -> dict[str, object]:
        return {
            "plan_hash": self.plan_hash,
            "feed_service_ids": [service.service_id for service in self.services],
            "monitor_service_ids": [
                _runtime_feed_monitor_service_id(service) for service in self.services
            ],
        }

    def managed_services(
        self,
        runner_factory: Callable[[RuntimeFeedServicePlan], Callable[[], Awaitable[None]]] | None = None,
    ) -> tuple[ManagedServiceSpec, ...]:
        factory = runner_factory or _unconfigured_feed_runner
        return tuple(
            ManagedServiceSpec(
                service.service_id,
                factory(service),
                ServiceCriticality.CRITICAL,
                restart_limit=1,
            )
            for service in self.services
        )

    def managed_service_bundle(
        self,
        *,
        feed_runner_factory: Callable[[RuntimeFeedServicePlan], Callable[[], Awaitable[None]]],
        monitor_runner_factory: Callable[[RuntimeFeedServicePlan], Callable[[], Awaitable[None]]],
    ) -> "RuntimeFeedServiceBundle":
        if not callable(feed_runner_factory):
            raise ValueError("runtime feed service bundle requires a feed runner factory")
        if not callable(monitor_runner_factory):
            raise ValueError("runtime feed service bundle requires a monitor runner factory")
        specs = []
        for service in self.services:
            feed_runner = feed_runner_factory(service)
            monitor_runner = monitor_runner_factory(service)
            if not callable(feed_runner):
                raise ValueError(f"feed runner factory returned a non-callable runner for {service.service_id}")
            if not callable(monitor_runner):
                raise ValueError(f"monitor runner factory returned a non-callable runner for {service.service_id}")
            specs.extend((
                ManagedServiceSpec(
                    service.service_id,
                    feed_runner,
                    ServiceCriticality.CRITICAL,
                    restart_limit=1,
                ),
                ManagedServiceSpec(
                    _runtime_feed_monitor_service_id(service),
                    monitor_runner,
                    ServiceCriticality.CRITICAL,
                    restart_limit=1,
                ),
            ))
        return RuntimeFeedServiceBundle(self, tuple(specs))


@dataclass(frozen=True, slots=True)
class RuntimeFeedServiceBundle:
    plan: RuntimeFeedPlan
    services: tuple[ManagedServiceSpec, ...]

    def __post_init__(self) -> None:
        expected = {
            name
            for service in self.plan.services
            for name in (service.service_id, _runtime_feed_monitor_service_id(service))
        }
        actual = {service.name for service in self.services}
        if actual != expected:
            raise ValueError(f"runtime feed service bundle differs: missing={expected-actual}, extra={actual-expected}")

    @property
    def bundle_hash(self) -> str:
        material = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def manifest(self) -> dict[str, object]:
        return self.plan.service_bundle_manifest()


@dataclass(frozen=True, slots=True)
class RuntimeExecutionServicePlan:
    mode: RunMode
    execution_driver: str
    environment: str

    def __post_init__(self) -> None:
        if not self.execution_driver.strip() or not self.environment.strip():
            raise ValueError("runtime execution service plan requires driver and environment")

    @property
    def service_id(self) -> str:
        return f"execution:{self.mode.value}:{self.execution_driver}"

    def manifest(self) -> dict[str, str]:
        return {
            "service_id": self.service_id,
            "mode": self.mode.value,
            "execution_driver": self.execution_driver,
            "environment": self.environment,
        }


@dataclass(frozen=True, slots=True)
class RuntimeExecutionPlan:
    mode: RunMode
    services: tuple[RuntimeExecutionServicePlan, ...]

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.PAPER_TRADING, RunMode.LIVE}:
            raise ValueError("runtime execution plan is only valid for paper/live modes")
        if not self.services:
            raise ValueError("paper/live runtime execution plan requires at least one execution driver")
        service_ids = [item.service_id for item in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("runtime execution plan service ids must be unique")

    @property
    def plan_hash(self) -> str:
        material = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def manifest(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "services": [item.manifest() for item in self.services],
        }

    def managed_services(
        self,
        runner_factory: Callable[[RuntimeExecutionServicePlan], Callable[[], Awaitable[None]]] | None = None,
    ) -> tuple[ManagedServiceSpec, ...]:
        factory = runner_factory or _unconfigured_execution_runner
        return tuple(
            ManagedServiceSpec(
                service.service_id,
                factory(service),
                ServiceCriticality.CRITICAL,
                restart_limit=1,
            )
            for service in self.services
        )


@dataclass(frozen=True, slots=True)
class RuntimeStrategyServicePlan:
    mode: RunMode
    strategy_id: str
    target_hash: str

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.target_hash.strip():
            raise ValueError("runtime strategy service plan requires strategy id and target hash")

    @property
    def service_id(self) -> str:
        return f"strategy:{self.mode.value}:{self.strategy_id}"

    def manifest(self) -> dict[str, str]:
        return {
            "service_id": self.service_id,
            "mode": self.mode.value,
            "strategy_id": self.strategy_id,
            "target_hash": self.target_hash,
        }


@dataclass(frozen=True, slots=True)
class RuntimeStrategyPlan:
    mode: RunMode
    services: tuple[RuntimeStrategyServicePlan, ...]

    def __post_init__(self) -> None:
        if self.mode not in {RunMode.PAPER_TRADING, RunMode.LIVE}:
            raise ValueError("runtime strategy plan is only valid for paper/live modes")
        if not self.services:
            raise ValueError("paper/live runtime strategy plan requires at least one strategy target")
        service_ids = [item.service_id for item in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("runtime strategy plan service ids must be unique")

    @property
    def plan_hash(self) -> str:
        material = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return sha256(material.encode()).hexdigest()

    def manifest(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "services": [item.manifest() for item in self.services],
        }

    def managed_services(
        self,
        runner_factory: Callable[[RuntimeStrategyServicePlan], Callable[[], Awaitable[None]]] | None = None,
    ) -> tuple[ManagedServiceSpec, ...]:
        factory = runner_factory or _unconfigured_strategy_runner
        return tuple(
            ManagedServiceSpec(
                service.service_id,
                factory(service),
                ServiceCriticality.CRITICAL,
                restart_limit=1,
            )
            for service in self.services
        )


def _runtime_feed_service_id(service: RuntimeFeedServicePlan) -> str:
    return f"feed:{service.name}:{service.live_view_id}"


def _runtime_feed_monitor_service_id(service: RuntimeFeedServicePlan) -> str:
    return f"feed-monitor:{service.name}:{service.live_view_id}"


@dataclass(frozen=True,slots=True)
class ComponentBinding:
    component_id:str
    instance:object
    def __post_init__(self):
        if not self.component_id.strip() or self.instance is None:raise ValueError("run component binding requires id and instance")


@dataclass(frozen=True,slots=True)
class ExecutableRunComposition:
    declaration:RunModeComposition
    bindings:tuple[ComponentBinding,...]
    runner:Callable[[],object]
    def __post_init__(self):
        expected={self.declaration.event_source,self.declaration.clock,self.declaration.execution_driver,
            self.declaration.persistence,self.declaration.safety_policy};actual={item.component_id for item in self.bindings}
        if actual!=expected:raise ValueError(f"run composition bindings differ: missing={expected-actual}, extra={actual-expected}")
        if not callable(self.runner):raise ValueError("executable run composition requires runner")
    def run(self):return self.runner()
    @property
    def composition_hash(self):return self.declaration.composition_hash


def backtest_composition() -> RunModeComposition:
    return RunModeComposition(
        RunMode.BACKTEST, "frozen-release", "replay", "fill-model", "backtest-artifact",
        "backtest-gates", CapturePolicy.NONE,
    )


def historical_simulation_composition() -> RunModeComposition:
    return RunModeComposition(
        RunMode.HISTORICAL_SIMULATION, "frozen-release-async", "replay", "simulated-venue",
        "runtime-store", "simulation-gates", CapturePolicy.CANONICAL,
    )


def paper_trading_composition(provider: str) -> RunModeComposition:
    return RunModeComposition(
        RunMode.PAPER_TRADING, f"live:{provider}", "system", "simulated", "runtime-store",
        "paper-runtime-gates", CapturePolicy.RAW_AND_CANONICAL,
    )

def live_composition(provider: str, execution_driver: str) -> RunModeComposition:
    return RunModeComposition(
        RunMode.LIVE, f"live:{provider}", "system", execution_driver, "runtime-store",
        "live-runtime-gates", CapturePolicy.RAW_AND_CANONICAL,
    )


def runtime_feed_plan(mode: RunMode | str, feed_bindings: tuple[Mapping[str, object], ...]) -> RuntimeFeedPlan:
    run_mode = _runtime_mode(mode)
    services = []
    for binding in feed_bindings:
        gate = binding.get("freshness_gate")
        if not isinstance(gate, Mapping) or not gate.get("passed"):
            raise ValueError(f"feed binding {binding.get('name')!r} did not pass freshness gate")
        services.append(RuntimeFeedServicePlan(
            str(binding.get("name") or ""),
            str(binding.get("dataset") or ""),
            str(binding.get("live_view_id") or ""),
            str(binding.get("event_source_contract") or ""),
            str(binding.get("channel_contract") or ""),
            CapturePolicy.RAW_AND_CANONICAL,
        ))
    return RuntimeFeedPlan(run_mode, tuple(services))


def runtime_execution_plan(mode: RunMode | str, composition: RunModeComposition) -> RuntimeExecutionPlan:
    run_mode = _runtime_mode(mode)
    environment = "live" if run_mode is RunMode.LIVE else "paper"
    return RuntimeExecutionPlan(run_mode, (
        RuntimeExecutionServicePlan(run_mode, composition.execution_driver, environment),
    ))


def runtime_strategy_plan(mode: RunMode | str, *, strategy_id: str, target_hash: str) -> RuntimeStrategyPlan:
    run_mode = _runtime_mode(mode)
    return RuntimeStrategyPlan(run_mode, (
        RuntimeStrategyServicePlan(run_mode, strategy_id, target_hash),
    ))


def _runtime_mode(mode: RunMode | str) -> RunMode:
    raw = mode.value if isinstance(mode, RunMode) else str(mode)
    if raw == "paper":
        return RunMode.PAPER_TRADING
    return RunMode(raw)


def _unconfigured_feed_runner(service: RuntimeFeedServicePlan) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        raise RuntimeError(f"feed service {service.name!r} has no runtime connector bound")

    return run


def _unconfigured_execution_runner(service: RuntimeExecutionServicePlan) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        raise RuntimeError(f"execution service {service.execution_driver!r} has no runtime gateway bound")

    return run


def _unconfigured_strategy_runner(service: RuntimeStrategyServicePlan) -> Callable[[], Awaitable[None]]:
    async def run() -> None:
        raise RuntimeError(f"strategy service {service.strategy_id!r} has no runtime runner bound")

    return run
