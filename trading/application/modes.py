from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from trading.data.models import RunMode
from trading.market_data.subscriptions import CapturePolicy


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
        live = self.mode in {RunMode.LIVE_PAPER, RunMode.LIVE}
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


def live_paper_composition(provider: str) -> RunModeComposition:
    return RunModeComposition(
        RunMode.LIVE_PAPER, f"live:{provider}", "system", "simulated", "runtime-store",
        "paper-runtime-gates", CapturePolicy.RAW_AND_CANONICAL,
    )


def live_composition(provider: str, execution_driver: str) -> RunModeComposition:
    return RunModeComposition(
        RunMode.LIVE, f"live:{provider}", "system", execution_driver, "runtime-store",
        "live-runtime-gates", CapturePolicy.RAW_AND_CANONICAL,
    )
