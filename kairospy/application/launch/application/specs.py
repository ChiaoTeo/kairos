"""Typed launch request adapters.

These types are configuration-writing conveniences.  They deliberately map
to the same LaunchConfig and LaunchPlan used by the TOML/CLI surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ...data import DatasetSetRef
from .configuration import LaunchConfig, LaunchConfigurationApplication
from .semantics import OptionBacktestConstraints


@dataclass(frozen=True, slots=True)
class BacktestSpec:
    strategy: str
    data: DatasetSetRef
    account: str
    risk_profile: str
    start: str
    end: str
    seed: int
    launch_id: str = "programmatic-backtest"
    strategy_params: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)
    option_constraints: OptionBacktestConstraints | None = None

    def __post_init__(self) -> None:
        for name in (
            "strategy",
            "account",
            "risk_profile",
            "start",
            "end",
            "launch_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"backtest {name} is required")
        if ":" not in self.strategy:
            raise ValueError("backtest strategy must be a module:callable reference")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("backtest seed must be an integer")

    def as_launch_values(self) -> dict[str, Any]:
        backtest: dict[str, Any] = {
            "seed": self.seed,
            "data": self.data.as_dict(),
            "market": {
                "start": self.start,
                "end": self.end,
                "scope": "instance",
                "profile": "replay",
            },
        }
        if self.option_constraints is not None:
            backtest["option_constraints"] = self.option_constraints.as_dict()
        return {
            "launch": {
                "id": self.launch_id,
                "mode": "backtest",
                "strategy": self.strategy,
            },
            "strategy": {"params": dict(self.strategy_params)},
            "account": {"ref": self.account},
            "risk": {"profile": self.risk_profile},
            "execution": dict(self.execution),
            "backtest": backtest,
        }

    def to_launch_config(
        self,
        *,
        workspace_root: str | Path,
    ) -> LaunchConfig:
        return LaunchConfigurationApplication().from_values(
            self.as_launch_values(),
            workspace_root=workspace_root,
            source_name=self.launch_id,
        )


@dataclass(frozen=True, slots=True)
class BacktestResult:
    launch_id: str
    instance_id: str
    status: str
    normalized_config_hash: str
    report: Mapping[str, Any]


__all__ = ["BacktestResult", "BacktestSpec", "OptionBacktestConstraints"]
