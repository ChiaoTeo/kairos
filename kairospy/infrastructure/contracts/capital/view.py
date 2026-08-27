"""Capital v2 current-view contract and application current-view queries."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.infrastructure.protocol.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.indexed_view import (
    IndexedViewReader,
    IndexedViewSchema,
)

sys.modules.setdefault("kairos", _generated_kairos)


STATE_DATABASE = "state"
OBJECTIVES_DATABASE = "objectives"
DEMANDS_DATABASE = "demands"
POLICIES_DATABASE = "policies"
FACTS_DATABASE = "facts"
AVAILABILITY_DATABASE = "availability"
ROUTES_DATABASE = "routes"
PLANS_DATABASE = "plans"
RESERVATIONS_DATABASE = "reservations"
OPERATIONS_DATABASE = "operations"
ALERTS_DATABASE = "alerts"
_DATABASES = (
    STATE_DATABASE,
    OBJECTIVES_DATABASE,
    DEMANDS_DATABASE,
    POLICIES_DATABASE,
    FACTS_DATABASE,
    AVAILABILITY_DATABASE,
    ROUTES_DATABASE,
    PLANS_DATABASE,
    RESERVATIONS_DATABASE,
    OPERATIONS_DATABASE,
    ALERTS_DATABASE,
)
_ROOTS = {
    STATE_DATABASE: ("CSM3", "CapitalStateCurrent"),
    OBJECTIVES_DATABASE: ("CFO3", "CapitalObjectiveCurrent"),
    DEMANDS_DATABASE: ("CDM3", "CapitalDemandCurrent"),
    POLICIES_DATABASE: ("CPC3", "CapitalPolicyCurrent"),
    FACTS_DATABASE: ("CFC3", "CapitalFactsCurrent"),
    AVAILABILITY_DATABASE: ("CAV3", "CapitalAvailabilityCurrent"),
    ROUTES_DATABASE: ("CRT3", "CapitalRouteCurrent"),
    PLANS_DATABASE: ("CPL3", "CapitalPlanCurrent"),
    RESERVATIONS_DATABASE: ("CRS3", "CapitalReservationCurrent"),
    OPERATIONS_DATABASE: ("COP3", "CapitalOperationCurrent"),
    ALERTS_DATABASE: ("CAL3", "CapitalAlertCurrent"),
}
_SCHEMAS = tuple(
    IndexedViewSchema(database, 1, _ROOTS[database][0], 1) for database in _DATABASES
)
_PREFIX = b"\x01"
MAX_INDEXED_VALUES_PER_DATABASE = 100_000


def capital_indexed_environment_path(root: str | Path, capital_group_id: str) -> Path:
    return (
        Path(root)
        / "views"
        / "v3"
        / "Capital"
        / f"capital-{_component(capital_group_id)}"
        / "epoch-1"
        / "current.lmdb"
    )


class CapitalIndexedViewQueries:
    """Read Capital availability without using its JSON control plane."""

    def __init__(
        self,
        root: str | Path,
        capital_group_id: str,
        *,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
    ) -> None:
        self.capital_group_id = capital_group_id
        self._reader = IndexedViewReader(
            capital_indexed_environment_path(root, capital_group_id),
            map_size=256 * 1024 * 1024,
            workspace_id=workspace_id,
            launch_id=launch_id,
            instance_id=instance_id,
            owner="Capital",
            publisher_resource_id=f"capital-{capital_group_id}",
            resource_epoch=1,
            schemas=_SCHEMAS,
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def _snapshot(self) -> tuple[Any, dict[str, tuple[Any, ...]]]:
        snapshot = self._reader.snapshot(
            tuple(
                (
                    database,
                    _PREFIX,
                    MAX_INDEXED_VALUES_PER_DATABASE + 1,
                )
                for database in _DATABASES
            )
        )
        values: dict[str, tuple[Any, ...]] = {}
        for database, rows in snapshot.rows.items():
            if len(rows) > MAX_INDEXED_VALUES_PER_DATABASE:
                raise RuntimeError(
                    f"Capital indexed database {database} exceeds its read bound"
                )
            identifier, root_name = _ROOTS[database]
            decoded = []
            for _, payload in rows:
                if len(payload) < 8 or payload[4:8] != identifier.encode():
                    raise ValueError(f"invalid Capital indexed value for {database}")
                module = __import__(
                    f"kairospy.infrastructure.protocol.generated.kairos.capital.v2.{root_name}",
                    fromlist=[root_name],
                )
                root = getattr(module, root_name).GetRootAs(payload, 0)
                if _text(root.CapitalGroupId()) != self.capital_group_id:
                    raise ValueError("Capital indexed group identity mismatch")
                value = root if database == STATE_DATABASE else root.Value()
                if value is None:
                    raise ValueError(f"{root_name} is missing its required value")
                decoded.append(value)
            values[database] = tuple(decoded)
        return snapshot.metadata, values

    def current(self) -> dict[str, Any]:
        metadata, values = self._snapshot()
        if len(values[STATE_DATABASE]) != 1:
            raise ValueError("Capital indexed snapshot must contain one state")
        state = values[STATE_DATABASE][0]
        availabilities = tuple(
            _availability(value, self.capital_group_id)
            for value in values[AVAILABILITY_DATABASE]
        )
        alerts = tuple(_recovery_alert(value) for value in values[ALERTS_DATABASE])
        return {
            "capital_group_id": self.capital_group_id,
            "kind": "current",
            "generation": int(state.EventSequence()),
            "applied_event_sequence": metadata.applied_event_sequence,
            "path": str(self.path),
            "strategy_id": _text(state.StrategyId()),
            "environment": _text(state.Environment()),
            "membership_version": int(state.MembershipVersion()),
            "event_sequence": int(state.EventSequence()),
            "journal_sequence": int(state.JournalSequence()),
            "summary": {
                "objective_count": len(values[OBJECTIVES_DATABASE]),
                "demand_count": len(values[DEMANDS_DATABASE]),
                "policy_count": len(values[POLICIES_DATABASE]),
                "facts_count": len(values[FACTS_DATABASE]),
                "availability_count": len(availabilities),
                "route_count": len(values[ROUTES_DATABASE]),
                "plan_count": len(values[PLANS_DATABASE]),
                "reservation_count": len(values[RESERVATIONS_DATABASE]),
                "operation_count": len(values[OPERATIONS_DATABASE]),
                "alert_count": len(alerts),
                "ready_availability_count": sum(
                    1 for value in availabilities if value["readiness"] == "ready"
                ),
                "degraded_availability_count": sum(
                    1 for value in availabilities if value["readiness"] == "degraded"
                ),
                "critical_alert_count": sum(
                    1 for value in alerts if value["severity"] == "critical"
                ),
            },
            "availabilities": list(availabilities),
            "alerts": list(alerts),
        }

    def availabilities(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _availability(value, self.capital_group_id)
            for value in self._snapshot()[1][AVAILABILITY_DATABASE]
        )

    def objectives(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _objective(value) for value in self._snapshot()[1][OBJECTIVES_DATABASE]
        )

    def demands(self) -> tuple[dict[str, Any], ...]:
        return tuple(_demand(value) for value in self._snapshot()[1][DEMANDS_DATABASE])

    def plans(self) -> tuple[dict[str, Any], ...]:
        return tuple(_plan(value) for value in self._snapshot()[1][PLANS_DATABASE])

    def routes(self) -> tuple[dict[str, Any], ...]:
        return tuple(_route(value) for value in self._snapshot()[1][ROUTES_DATABASE])

    def reservations(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _reservation(value) for value in self._snapshot()[1][RESERVATIONS_DATABASE]
        )

    def operations(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _operation(value) for value in self._snapshot()[1][OPERATIONS_DATABASE]
        )

    def alerts(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _recovery_alert(value) for value in self._snapshot()[1][ALERTS_DATABASE]
        )

    def availability(
        self,
        *,
        capital_group_id: str | None,
        location: object | None,
    ) -> dict[str, Any]:
        if capital_group_id != self.capital_group_id:
            raise ValueError("Capital current view belongs to another capital group")
        values = self.availabilities()
        if location is None:
            if len(values) != 1:
                raise ValueError(
                    "Capital location is required when the group has multiple locations"
                )
            return values[0]
        expected_location = _location_query(location)
        for value in values:
            if value["location"] == expected_location:
                return value
        raise LookupError("Capital location has not been evaluated")


def _availability(value: object | None, capital_group_id: str) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty availability entry")
    row = cast(Any, value)
    location = row.Destination()
    if location is None:
        raise ValueError("Capital availability destination is missing")
    readiness = {
        0: "waiting_for_facts",
        1: "degraded",
        2: "ready",
        3: "waiting_for_accounts",
    }.get(int(row.Readiness()))
    if readiness is None:
        raise ValueError(f"unknown Capital readiness: {row.Readiness()}")
    return {
        "capital_group_id": capital_group_id,
        "readiness": readiness,
        "location": _location(location),
        "policy_version": int(row.PolicyVersion()),
        "active_objective_ids": list(_strings(row, "ActiveObjectiveIds")),
        "active_demand_ids": list(_strings(row, "ActiveDemandIds")),
        "funding_horizons": list(
            _funding_horizon(row.FundingHorizons(index))
            for index in range(int(row.FundingHorizonsLength()))
        ),
        "desired_target": str(_decimal(row.DesiredTarget())),
        "observed_available": str(_decimal(row.ObservedAvailable())),
        "effective_target": str(_decimal(row.EffectiveTarget())),
        "deficit": str(_decimal(row.Deficit())),
        "account_watermark": int(row.AccountWatermark()),
        "risk_policy_version": int(row.RiskPolicyVersion()),
        "risk_watermark": int(row.RiskWatermark()),
        "reason": _text(row.Reason()),
    }


def _funding_horizon(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty funding horizon")
    row = cast(Any, value)
    return {
        "required_by_unix_nanos": int(row.RequiredByUnixNanos()),
        "objective_ids": list(_strings(row, "ObjectiveIds")),
        "demand_ids": list(_strings(row, "DemandIds")),
        "desired_available": str(_decimal(row.DesiredAvailable())),
    }


def _objective(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty objective")
    row = cast(Any, value)
    destination = row.Destination()
    if destination is None:
        raise ValueError("Capital objective destination is missing")
    return {
        "objective_id": _required_text(row.ObjectiveId(), "objective_id"),
        "version": int(row.Version()),
        "strategy_id": _required_text(row.StrategyId(), "strategy_id"),
        "destination": _location(destination),
        "desired_available": str(_decimal(row.DesiredAvailable())),
        "required_by_unix_nanos": int(row.RequiredByUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
        "priority": _enum(
            int(row.Priority()),
            {
                0: "low",
                1: "normal",
                2: "high",
                3: "critical",
            },
            "funding priority",
        ),
        "confidence_bps": int(row.ConfidenceBps()),
        "strategy_decision_id": _required_text(
            row.StrategyDecisionId(), "strategy_decision_id"
        ),
        "status": _enum(
            int(row.Status()),
            {
                0: "active",
                1: "cancelled",
                2: "expired",
            },
            "funding objective status",
        ),
        "updated_at_unix_nanos": int(row.UpdatedAtUnixNanos()),
    }


def _demand(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty demand")
    row = cast(Any, value)
    destination = row.Destination()
    if destination is None:
        raise ValueError("Capital demand destination is missing")
    return {
        "demand_id": _required_text(row.DemandId(), "demand_id"),
        "idempotency_key": _required_text(row.IdempotencyKey(), "idempotency_key"),
        "strategy_id": _required_text(row.StrategyId(), "strategy_id"),
        "destination": _location(destination),
        "observed_shortfall": str(_decimal(row.ObservedShortfall())),
        "observed_at_unix_nanos": int(row.ObservedAtUnixNanos()),
        "required_by_unix_nanos": int(row.RequiredByUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
        "priority": _enum(
            int(row.Priority()),
            {
                0: "low",
                1: "normal",
                2: "high",
                3: "critical",
            },
            "funding priority",
        ),
        "confidence_bps": int(row.ConfidenceBps()),
        "account_watermark": int(row.AccountWatermark()),
        "risk_watermark": int(row.RiskWatermark()),
        "launch_id": _required_text(row.LaunchId(), "launch_id"),
        "instance_id": _required_text(row.InstanceId(), "instance_id"),
        "causal_references": _strings(row, "CausalReferences"),
        "status": _enum(
            int(row.Status()),
            {
                0: "active",
                1: "expired",
            },
            "capital demand status",
        ),
        "updated_at_unix_nanos": int(row.UpdatedAtUnixNanos()),
    }


def _plan(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty plan")
    row = cast(Any, value)
    source = row.Source()
    destination = row.Destination()
    if source is None or destination is None:
        raise ValueError("Capital plan source or destination is missing")
    redemption = row.RedemptionObservedAvailable()
    return {
        "plan_id": _required_text(row.PlanId(), "plan_id"),
        "rebalance_decision_id": _required_text(
            row.RebalanceDecisionId(), "rebalance_decision_id"
        ),
        "route_id": _required_text(row.RouteId(), "route_id"),
        "route_version": int(row.RouteVersion()),
        "route_kind": _enum(
            int(row.RouteKind()),
            {
                0: "internal_transfer",
                1: "account_transfer",
                2: "earn_redemption_then_transfer",
                3: "earn_subscription",
            },
            "capital route kind",
        ),
        "source": _location(source),
        "destination": _location(destination),
        "amount": str(_decimal(row.Amount())),
        "objective_ids": _strings(row, "ObjectiveIds"),
        "demand_ids": _strings(row, "DemandIds"),
        "reservation_id": _required_text(row.ReservationId(), "reservation_id"),
        "idempotency_key": _required_text(row.IdempotencyKey(), "idempotency_key"),
        "selected_earn_product_id": _text(row.SelectedEarnProductId()),
        "source_account_watermark": int(row.SourceAccountWatermark()),
        "destination_account_watermark": int(row.DestinationAccountWatermark()),
        "source_observed_available": str(_decimal(row.SourceObservedAvailable())),
        "destination_observed_available": str(
            _decimal(row.DestinationObservedAvailable())
        ),
        "redemption_account_watermark": row.RedemptionAccountWatermark(),
        "redemption_observed_available": None
        if redemption is None
        else str(_decimal(redemption)),
        "earn_principal_before": str(_decimal(row.EarnPrincipalBefore())),
        "status": _enum(
            int(row.Status()),
            {
                0: "authorized",
                1: "transferring",
                2: "awaiting_transfer",
                3: "reconciling",
                4: "available",
                5: "completed",
                6: "indeterminate",
                7: "rejected",
                8: "expired",
                9: "failed",
                10: "redeeming",
                11: "awaiting_redemption",
                12: "subscribing",
                13: "awaiting_subscription",
            },
            "capital plan status",
        ),
        "recovery_action": _enum(
            int(row.RecoveryAction()),
            {
                0: "none",
                1: "no_compensation_required",
                2: "reconcile_original_operation",
                3: "hold_and_review",
            },
            "capital recovery action",
        ),
        "recovery_reason": _text(row.RecoveryReason()),
        "recovery_decided_at_unix_nanos": row.RecoveryDecidedAtUnixNanos(),
        "created_at_unix_nanos": int(row.CreatedAtUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
    }


def _route(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty route")
    row = cast(Any, value)
    source = row.Source()
    destination = row.Destination()
    if source is None or destination is None:
        raise ValueError("Capital route source or destination is missing")
    return {
        "route_id": _required_text(row.RouteId(), "route_id"),
        "version": int(row.Version()),
        "source": _location(source),
        "destination": _location(destination),
        "kind": _enum(
            int(row.Kind()),
            {
                0: "internal_transfer",
                1: "account_transfer",
                2: "earn_redemption_then_transfer",
                3: "earn_subscription",
            },
            "capital route kind",
        ),
        "per_operation_limit": str(_decimal(row.PerOperationLimit())),
        "daily_limit": str(_decimal(row.DailyLimit())),
        "required_source_authority": _required_text(
            row.RequiredSourceAuthority(), "required_source_authority"
        ),
        "settlement_class": _enum(
            int(row.SettlementClass()),
            {
                0: "immediate_book_transfer",
                1: "participant_history_then_account_observation",
            },
            "capital settlement class",
        ),
        "enabled": bool(row.Enabled()),
        "earn_product_id": _text(row.EarnProductId()),
        "demand_guard_nanos": int(row.DemandGuardNanos()),
        "allow_unknown_redemption_quota": bool(row.AllowUnknownRedemptionQuota()),
    }


def _reservation(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty reservation")
    row = cast(Any, value)
    source = row.Source()
    if source is None:
        raise ValueError("Capital reservation source is missing")
    return {
        "reservation_id": _required_text(row.ReservationId(), "reservation_id"),
        "plan_id": _required_text(row.PlanId(), "plan_id"),
        "source": _location(source),
        "amount": str(_decimal(row.Amount())),
        "source_account_watermark": int(row.SourceAccountWatermark()),
        "status": _enum(
            int(row.Status()),
            {
                0: "active",
                1: "consumed",
                2: "released",
                3: "expired",
            },
            "capital reservation status",
        ),
        "created_at_unix_nanos": int(row.CreatedAtUnixNanos()),
        "expires_at_unix_nanos": int(row.ExpiresAtUnixNanos()),
    }


def _operation(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty operation")
    row = cast(Any, value)
    return {
        "operation_id": _required_text(row.OperationId(), "operation_id"),
        "plan_id": _required_text(row.PlanId(), "plan_id"),
        "idempotency_key": _required_text(row.IdempotencyKey(), "idempotency_key"),
        "operation_index": int(row.OperationIndex()),
        "kind": _enum(
            int(row.Kind()),
            {
                0: "transfer",
                1: "earn_redemption",
                2: "earn_subscription",
            },
            "capital operation kind",
        ),
        "status": _enum(
            int(row.Status()),
            {
                0: "prepared",
                1: "dispatching",
                2: "awaiting_participant",
                3: "indeterminate",
                4: "awaiting_account_observation",
                5: "settled",
                6: "expired",
                7: "rejected",
                8: "failed",
            },
            "capital operation status",
        ),
        "participant_operation_id": _text(row.ParticipantOperationId()),
        "participant_state": _text(row.ParticipantState()),
        "dispatch_started_at_unix_nanos": row.DispatchStartedAtUnixNanos(),
        "attempt_count": int(row.AttemptCount()),
        "failure_reason": _text(row.FailureReason()),
        "account_observation_watermark": row.AccountObservationWatermark(),
        "updated_at_unix_nanos": int(row.UpdatedAtUnixNanos()),
    }


def _recovery_alert(value: object | None) -> dict[str, Any]:
    if value is None:
        raise ValueError("Capital view contains an empty recovery alert")
    row = cast(Any, value)
    kind = {
        0: "reconciliation_required",
        1: "manual_review",
    }.get(int(row.Kind()))
    severity = {
        0: "warning",
        1: "critical",
    }.get(int(row.Severity()))
    recovery_action = {
        2: "reconcile_original_operation",
        3: "hold_and_review",
    }.get(int(row.RecoveryAction()))
    if kind is None or severity is None or recovery_action is None:
        raise ValueError("Capital recovery alert contains an unknown enum value")
    return {
        "alert_id": _required_text(row.AlertId(), "alert_id"),
        "plan_id": _required_text(row.PlanId(), "plan_id"),
        "operation_id": _text(row.OperationId()),
        "kind": kind,
        "severity": severity,
        "recovery_action": recovery_action,
        "message": _required_text(row.Message(), "message"),
        "opened_at_unix_nanos": int(row.OpenedAtUnixNanos()),
    }


def _location_query(value: object) -> dict[str, str]:
    return {
        "broker": str(getattr(value, "broker")),
        "account_id": str(getattr(value, "account_id")),
        "segment": str(getattr(value, "segment")),
        "asset": str(getattr(value, "asset")),
    }


def _location(value: Any) -> dict[str, str]:
    return {
        "broker": _required_text(value.Broker(), "broker"),
        "account_id": _required_text(value.AccountId(), "account_id"),
        "segment": _required_text(value.Segment(), "segment"),
        "asset": _required_text(value.Asset(), "asset"),
    }


def _strings(value: Any, name: str) -> tuple[str, ...]:
    return tuple(
        _required_text(getattr(value, name)(index), name)
        for index in range(int(getattr(value, f"{name}Length")()))
    )


def _decimal(value: object | None) -> Decimal:
    if value is None:
        raise ValueError("Capital decimal field is missing")
    item = cast(Any, value)
    scale = int(item.Scale())
    if not 0 <= scale <= 18:
        raise ValueError("Capital decimal scale exceeds 18")
    return Decimal(int(item.Mantissa())).scaleb(-scale)


def _required_text(value: bytes | None, name: str) -> str:
    text = _text(value)
    if text is None or not text:
        raise ValueError(f"Capital view field {name} is missing")
    return text


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _enum(value: int, names: dict[int, str], name: str) -> str:
    if value not in names:
        raise ValueError(f"unknown Capital {name}: {value}")
    return names[value]


def _component(value: str) -> str:
    return "".join(
        chr(byte)
        if (byte < 128 and chr(byte).isalnum()) or byte in b"-_."
        else f"%{byte:02X}"
        for byte in value.encode()
    )


__all__ = [
    "CapitalIndexedViewQueries",
    "capital_indexed_environment_path",
]
