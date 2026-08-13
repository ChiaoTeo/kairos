from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyIdentity:
    """Stable runtime identity exposed to strategy code."""

    strategy_id: str
    launch_id: str
    instance_id: str
