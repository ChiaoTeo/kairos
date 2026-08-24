from __future__ import annotations

from dataclasses import replace

from ..domain.identity import InstanceState, LaunchInstance
from kairospy.strategy.apps.runtime.application import StrategyApplication, StrategyStatus


class LaunchInstanceApplication:
    """Launch-owned lifecycle facade for one instance-owned StrategyApplication."""

    def __init__(
        self, instance: LaunchInstance, strategy_application: StrategyApplication
    ) -> None:
        if (
            strategy_application.launch_id != instance.identity.launch_id
            or strategy_application.instance_id != instance.instance_id
        ):
            raise ValueError("strategy application does not belong to launch instance")
        self.instance = instance
        self.strategy_application = strategy_application

    def start(self) -> StrategyStatus:
        self.instance = replace(self.instance, state=InstanceState.STARTING)
        try:
            status = self.strategy_application.start()
        except Exception:
            self.instance = replace(self.instance, state=InstanceState.FAILED)
            raise
        self.instance = replace(self.instance, state=InstanceState.RUNNING)
        return status

    def enable(self) -> StrategyStatus:
        return self.strategy_application.enable()

    def pause(self, reason: str = "paused by cli") -> StrategyStatus:
        return self.strategy_application.pause(reason)

    def resume(self) -> StrategyStatus:
        return self.strategy_application.resume()

    def refresh(self) -> StrategyStatus:
        return self.strategy_application.refresh()

    def stop(self) -> StrategyStatus:
        self.instance = replace(self.instance, state=InstanceState.STOPPING)
        status = self.strategy_application.stop()
        self.instance = replace(self.instance, state=InstanceState.STOPPED)
        return status

    def status(self) -> dict[str, object]:
        value = self.strategy_application.status
        return {
            "launch_id": self.instance.identity.launch_id,
            "instance_id": self.instance.instance_id,
            "mode": self.instance.identity.mode,
            "instance_state": self.instance.state.value,
            "strategy_id": value.strategy_id,
            "strategy_state": value.state.value,
            "reason": value.reason,
            "dispatch_sequence": value.dispatch_sequence,
            "control_socket": str(self.instance.control_socket),
            "readiness": value.readiness.value,
            "data_health": value.data_health.value,
            "subscription_count": value.subscription_count,
            "active_subscription_count": value.active_subscription_count,
            "first_event_received": value.first_event_received,
            "last_event_time": value.last_event_time.isoformat()
            if value.last_event_time
            else None,
            "last_event_kind": value.last_event_kind,
            "event_count": value.event_count,
            "subscriptions": [
                dict(subscription) for subscription in value.subscriptions
            ],
        }
