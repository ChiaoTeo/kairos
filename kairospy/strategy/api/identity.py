from __future__ import annotations

from dataclasses import dataclass

from kairospy.primitives.runtime import (
    InstanceIdRead,
    LaunchIdRead,
    StrategyIdRead,
)


@dataclass(frozen=True, slots=True, init=False)
class StrategyIdentity:
    """Stable runtime identity exposed to strategy code."""

    strategy_id: StrategyIdRead
    launch_id: LaunchIdRead
    instance_id: InstanceIdRead

    def __init__(self, strategy_id: str, launch_id: str, instance_id: str) -> None:
        for name, value in (
            ("strategy_id", strategy_id),
            ("launch_id", launch_id),
            ("instance_id", instance_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        # NewType constructors are identity functions: this adds static
        # semantics to runtime-owned strings without allocating wrapper DTOs.
        object.__setattr__(self, "strategy_id", StrategyIdRead(strategy_id))
        object.__setattr__(self, "launch_id", LaunchIdRead(launch_id))
        object.__setattr__(self, "instance_id", InstanceIdRead(instance_id))
