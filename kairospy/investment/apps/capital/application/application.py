from __future__ import annotations

import time
from collections.abc import Callable

from kairospy.contracts.capital.events import CapitalEventVariant
from kairospy.contracts.capital.types import (
    CancelFundingObjectiveRequest,
    CapitalControlClient,
    CapitalControlResponse,
    CapitalCurrentView,
    CapitalDemandResponse,
)
from kairospy.infrastructure.protocol import LiveEventSource
from kairospy.primitives.account import AccountId
from kairospy.primitives.capital import (
    CapitalDemandId,
    CapitalGroupId,
    FundingObjectiveId,
)
from kairospy.primitives.runtime import (
    InstanceId,
    LaunchId,
    RequestId,
    StrategyId,
)

from .models import (
    CapitalAvailability,
    CapitalDemand,
    CapitalDemandReceipt,
    CapitalReadiness,
    CapitalRecoveryAlert,
    FundingForecastObservation,
    FundingLocation,
    FundingObjective,
    FundingObjectiveReceipt,
    FundingObjectiveStatus,
)
from .mapping import (
    capital_demand_request,
    funding_objective_request,
    map_capital_alert,
    map_capital_availability,
)


class CapitalApplication:
    """Strategy-facing client for one authoritative Capital group."""

    def __init__(
        self,
        commands: CapitalControlClient | None,
        current_view: CapitalCurrentView | None,
        event_source: LiveEventSource[CapitalEventVariant] | None = None,
        *,
        strategy_id: str,
        launch_id: str,
        instance_id: str,
        capital_group_id: CapitalGroupId | str | None,
        account_ids: tuple[AccountId, ...] = (),
        account_lease_fences: dict[AccountId, str] | None = None,
        disabled_reason: str = "capital is disabled for this launch",
    ) -> None:
        if not strategy_id.strip() or not launch_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id, launch_id, and instance_id are required")
        if commands is not None and (
            capital_group_id is None or not str(capital_group_id).strip()
        ):
            raise ValueError("enabled Capital requires capital_group_id")
        self._commands = commands
        self._current_view = current_view
        self._event_source = event_source
        self._strategy_id = StrategyId(strategy_id)
        self._launch_id = LaunchId(launch_id)
        self._instance_id = InstanceId(instance_id)
        self._capital_group_id = (
            None
            if capital_group_id is None
            else CapitalGroupId(str(capital_group_id).strip())
        )
        self._account_ids = frozenset(account_ids)
        self._account_lease_fences = dict(account_lease_fences or {})
        self._disabled_reason = disabled_reason
        self._request_counter = 0
        self._event_cursor = 0
        self._event_cursor_key: tuple[str, str, int] | None = None
        self._notification_gap_count = 0
        self._notification_incarnation_change_count = 0

    @classmethod
    def disabled(
        cls,
        *,
        strategy_id: str,
        launch_id: str,
        instance_id: str,
        account_ids: tuple[AccountId, ...] = (),
    ) -> "CapitalApplication":
        return cls(
            None,
            None,
            None,
            strategy_id=strategy_id,
            launch_id=launch_id,
            instance_id=instance_id,
            capital_group_id=None,
            account_ids=account_ids,
        )

    def visit_live(
        self,
        visitor: Callable[[CapitalEventVariant], None],
        *,
        fragment_limit: int = 64,
    ) -> int:
        """Poll Capital once and consume callback-scoped events synchronously."""

        if self._event_source is None:
            return 0
        cursor = self._event_cursor

        def accept(event: CapitalEventVariant) -> None:
            nonlocal cursor
            metadata = event.metadata
            if metadata.stream_id != "capital.events":
                raise RuntimeError(
                    f"Capital event stream identity is invalid: {metadata.stream_id}"
                )
            if metadata.launch_id != self._launch_id:
                raise RuntimeError("Capital event belongs to another launch")
            if metadata.instance_id != self._instance_id:
                raise RuntimeError("Capital event belongs to another launch instance")
            cursor_key = (
                metadata.stream_id,
                str(metadata.producer),
                int(metadata.producer_incarnation),
            )
            sequence = int(metadata.sequence)
            if self._event_cursor_key is not None and cursor_key != self._event_cursor_key:
                self._notification_incarnation_change_count += 1
                cursor = sequence - 1
            elif self._event_cursor_key is None:
                cursor = sequence - 1
            self._event_cursor_key = cursor_key
            if sequence <= cursor:
                return
            if sequence != cursor + 1:
                self._notification_gap_count += 1
            cursor = sequence
            self._event_cursor = sequence
            visitor(event)

        return self._event_source.poll_visit(accept, fragment_limit=fragment_limit)

    def notification_health(self) -> dict[str, int]:
        return {
            "cursor": self._event_cursor,
            "gap_count": self._notification_gap_count,
            "incarnation_change_count": self._notification_incarnation_change_count,
        }

    def close_live(self) -> None:
        if self._event_source is not None:
            self._event_source.close()

    @property
    def enabled(self) -> bool:
        return self._commands is not None

    def account_lease_fence(self, account_id: AccountId) -> str | None:
        return self._account_lease_fences.get(account_id)

    def publish_objective(self, objective: FundingObjective) -> FundingObjectiveReceipt:
        scope_error = self._scope_error(objective.destination)
        if scope_error is not None:
            return self._rejected(objective, scope_error)
        if self._commands is None:
            return FundingObjectiveReceipt(
                objective.objective_id,
                objective.version,
                FundingObjectiveStatus.DISABLED,
                self._disabled_reason,
            )
        value = self._commands.publish_funding_objective(
            funding_objective_request(
                objective,
                request_id=self._request_id("capital.objective.publish"),
                capital_group_id=self._required_group_id(),
                strategy_id=self._strategy_id,
            )
        )
        return _receipt(value)

    def publish_forecast(
        self, forecast: FundingForecastObservation
    ) -> FundingObjectiveReceipt:
        """Publish typed Strategy forecast evidence as a liquidity objective."""
        return self.publish_objective(forecast.to_objective())

    def cancel_objective(
        self, objective_id: FundingObjectiveId | str, *, expected_version: int
    ) -> FundingObjectiveReceipt:
        normalized = FundingObjectiveId(str(objective_id))
        if expected_version <= 0:
            raise ValueError("Funding objective expected_version must be positive")
        if self._commands is None:
            return FundingObjectiveReceipt(
                normalized,
                expected_version,
                FundingObjectiveStatus.DISABLED,
                self._disabled_reason,
            )
        return _receipt(
            self._commands.cancel_funding_objective(
                CancelFundingObjectiveRequest(
                    str(self._request_id("capital.objective.cancel")),
                    str(self._required_group_id()),
                    str(normalized),
                    expected_version,
                    str(self._strategy_id),
                    time.time_ns(),
                )
            )
        )

    def observe_demand(self, demand: CapitalDemand) -> CapitalDemandReceipt:
        scope_error = self._scope_error(demand.destination)
        if scope_error is not None:
            return CapitalDemandReceipt(
                demand.demand_id, FundingObjectiveStatus.REJECTED, scope_error
            )
        if self._commands is None:
            return CapitalDemandReceipt(
                demand.demand_id,
                FundingObjectiveStatus.DISABLED,
                self._disabled_reason,
            )
        expected_fence = self._account_lease_fences.get(demand.destination.account_id)
        if expected_fence is None or expected_fence != demand.destination_lease_fence:
            return CapitalDemandReceipt(
                demand.demand_id,
                FundingObjectiveStatus.REJECTED,
                "Capital demand destination lease fence is stale or unavailable",
            )
        value = self._commands.observe_capital_demand(
            capital_demand_request(
                demand,
                request_id=self._request_id("capital.demand.observe"),
                capital_group_id=self._required_group_id(),
                strategy_id=self._strategy_id,
                launch_id=self._launch_id,
                instance_id=self._instance_id,
            )
        )
        return CapitalDemandReceipt(
            demand_id=CapitalDemandId(value.demand_id),
            status=FundingObjectiveStatus(str(value.status)),
            message=_control_message(value),
        )

    def availability(
        self, location: FundingLocation | None = None
    ) -> CapitalAvailability:
        if location is not None:
            scope_error = self._scope_error(location)
            if scope_error is not None:
                return CapitalAvailability(
                    self._capital_group_id,
                    CapitalReadiness.DEGRADED,
                    location=location,
                    reason=scope_error,
                )
        if self._current_view is None:
            return CapitalAvailability(
                self._capital_group_id,
                (
                    CapitalReadiness.DISABLED
                    if self._commands is None
                    else CapitalReadiness.DEGRADED
                ),
                location=location,
                reason=(
                    self._disabled_reason
                    if self._commands is None
                    else "Capital availability current_view is unavailable"
                ),
            )
        snapshot = self._current_view.snapshot()
        value = snapshot.availability(
            None
            if location is None
            else (
                str(location.broker),
                str(location.account_id),
                str(location.segment),
                str(location.asset),
            )
        )
        return map_capital_availability(
            value, capital_group_id=self._capital_group_id
        )

    def recovery_alerts(self) -> tuple[CapitalRecoveryAlert, ...]:
        if self._current_view is None:
            return ()
        return tuple(
            map_capital_alert(value)
            for value in self._current_view.snapshot().alerts
        )

    def _scope_error(self, location: FundingLocation) -> str | None:
        if self._account_ids and location.account_id not in self._account_ids:
            return f"Account '{location.account_id}' is outside this Strategy capital group"
        return None

    def _request_id(self, operation: str) -> RequestId:
        self._request_counter += 1
        return RequestId(
            f"{self._launch_id}:{self._instance_id}:{self._strategy_id}:"
            f"{operation}:{self._request_counter}"
        )

    def _required_group_id(self) -> CapitalGroupId:
        if self._capital_group_id is None:
            raise RuntimeError("Capital group is unavailable")
        return self._capital_group_id

    @staticmethod
    def _rejected(objective: FundingObjective, message: str) -> FundingObjectiveReceipt:
        return FundingObjectiveReceipt(
            objective.objective_id,
            objective.version,
            FundingObjectiveStatus.REJECTED,
            message,
        )


def _receipt(
    value: FundingObjectiveReceipt | CapitalControlResponse,
) -> FundingObjectiveReceipt:
    if isinstance(value, FundingObjectiveReceipt):
        return value
    return FundingObjectiveReceipt(
        objective_id=FundingObjectiveId(value.objective_id),
        version=value.version,
        status=FundingObjectiveStatus(value.status),
        message=_control_message(value),
    )


def _control_message(
    value: CapitalControlResponse | CapitalDemandResponse,
) -> str | None:
    return value.error_message
