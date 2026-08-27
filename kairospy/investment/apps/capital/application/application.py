from __future__ import annotations

import time
from typing import Any

from kairospy.infrastructure.contracts.capital.types import (
    CancelFundingObjectiveRequest,
)
from kairospy.primitives.account import AccountId

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
        commands: Any | None,
        current_view: Any | None,
        *,
        strategy_id: str,
        launch_id: str,
        instance_id: str,
        capital_group_id: str | None,
        account_ids: tuple[AccountId, ...] = (),
        account_lease_fences: dict[AccountId, str] | None = None,
        disabled_reason: str = "capital is disabled for this launch",
    ) -> None:
        if not strategy_id.strip() or not launch_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id, launch_id, and instance_id are required")
        if commands is not None and not (capital_group_id or "").strip():
            raise ValueError("enabled Capital requires capital_group_id")
        self._commands = commands
        self._current_view = current_view
        self._strategy_id = strategy_id
        self._launch_id = launch_id
        self._instance_id = instance_id
        self._capital_group_id = (
            None if capital_group_id is None else capital_group_id.strip()
        )
        self._account_ids = frozenset(account_ids)
        self._account_lease_fences = dict(account_lease_fences or {})
        self._disabled_reason = disabled_reason
        self._request_counter = 0

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
            strategy_id=strategy_id,
            launch_id=launch_id,
            instance_id=instance_id,
            capital_group_id=None,
            account_ids=account_ids,
        )

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
        self, objective_id: str, *, expected_version: int
    ) -> FundingObjectiveReceipt:
        normalized = objective_id.strip()
        if not normalized:
            raise ValueError("Funding objective id is required")
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
                    self._request_id("capital.objective.cancel"),
                    self._required_group_id(),
                    normalized,
                    expected_version,
                    self._strategy_id,
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
            demand_id=str(value.demand_id),
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
        value = self._current_view.availability(
            capital_group_id=self._capital_group_id,
            location=location,
        )
        return map_capital_availability(value)

    def recovery_alerts(self) -> tuple[CapitalRecoveryAlert, ...]:
        if self._current_view is None:
            return ()
        return tuple(map_capital_alert(value) for value in self._current_view.alerts())

    def _scope_error(self, location: FundingLocation) -> str | None:
        if self._account_ids and location.account_id not in self._account_ids:
            return f"Account '{location.account_id}' is outside this Strategy capital group"
        return None

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        return (
            f"{self._launch_id}:{self._instance_id}:{self._strategy_id}:"
            f"{operation}:{self._request_counter}"
        )

    def _required_group_id(self) -> str:
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
    value: FundingObjectiveReceipt | object,
) -> FundingObjectiveReceipt:
    if isinstance(value, FundingObjectiveReceipt):
        return value
    version = getattr(value, "version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("Funding objective receipt version must be an integer")
    return FundingObjectiveReceipt(
        objective_id=str(getattr(value, "objective_id")),
        version=version,
        status=FundingObjectiveStatus(str(getattr(value, "status"))),
        message=_control_message(value),
    )


def _control_message(value: object) -> str | None:
    message = getattr(value, "error_message", None)
    return None if message is None else str(message)
