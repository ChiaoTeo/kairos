from __future__ import annotations

from dataclasses import replace

from ..domain.identity import InstanceState, LaunchInstance
from ...strategy.application import StrategyHost, StrategyHostStatus


class LaunchInstanceApplication:
    """Launch-owned lifecycle facade for one instance-owned StrategyHost."""

    def __init__(self, instance: LaunchInstance, strategy_host: StrategyHost) -> None:
        if strategy_host.launch_id != instance.identity.launch_id or strategy_host.instance_id != instance.instance_id:
            raise ValueError("strategy host does not belong to launch instance")
        self.instance = instance
        self.strategy_host = strategy_host

    def start(self) -> StrategyHostStatus:
        self.instance = replace(self.instance, state=InstanceState.STARTING)
        try:
            status = self.strategy_host.start()
        except Exception:
            self.instance = replace(self.instance, state=InstanceState.FAILED)
            raise
        self.instance = replace(self.instance, state=InstanceState.RUNNING)
        return status

    def enable(self) -> StrategyHostStatus:
        return self.strategy_host.enable()

    def pause(self, reason: str = "paused by cli") -> StrategyHostStatus:
        return self.strategy_host.pause(reason)

    def resume(self) -> StrategyHostStatus:
        return self.strategy_host.resume()

    def refresh(self) -> StrategyHostStatus:
        return self.strategy_host.refresh()

    def stop(self) -> StrategyHostStatus:
        self.instance = replace(self.instance, state=InstanceState.STOPPING)
        status = self.strategy_host.stop()
        self.instance = replace(self.instance, state=InstanceState.STOPPED)
        return status

    def status(self) -> dict[str, object]:
        value = self.strategy_host.status
        return {
            "launch_id": self.instance.identity.launch_id,
            "instance_id": self.instance.instance_id,
            "mode": self.instance.identity.mode,
            "instance_state": self.instance.state.value,
            "strategy_id": value.strategy_id,
            "strategy_state": value.state.value,
            "reason": value.reason,
            "event_sequence": value.event_sequence,
            "control_socket": str(self.instance.control_socket),
        }

