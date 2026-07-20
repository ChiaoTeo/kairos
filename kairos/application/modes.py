from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from kairos.data.contracts import RunMode
from kairos.market_data.subscriptions import CapturePolicy


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


def research_composition() -> RunModeComposition:
    return RunModeComposition(
        RunMode.RESEARCH, "frozen-release", "analysis", "none", "study-artifact",
        "research-validation", CapturePolicy.NONE,
    )


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
