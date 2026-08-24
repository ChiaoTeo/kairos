"""Capital v2 current-view contract and application current-view queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, cast

from kairospy.application.capital.models import (
    CapitalAlertKind,
    CapitalAlertSeverity,
    CapitalAvailability,
    CapitalFundingHorizon,
    CapitalReadiness,
    CapitalRecoveryAction,
    CapitalRecoveryAlert,
    FundingLocation,
)
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.shared_snapshot import SharedSnapshotReader

sys.modules.setdefault("kairos", _generated_kairos)


@dataclass(frozen=True, slots=True)
class CapitalViewKey:
    capital_group_id: str

    def __post_init__(self) -> None:
        if not self.capital_group_id.strip():
            raise ValueError("Capital view capital_group_id is required")

    def canonical_key(self) -> str:
        return f"capital.current/{_component(self.capital_group_id)}"

    def resource_path(self, root: str | Path) -> Path:
        return (
            Path(root)
            / "capital"
            / _component(self.capital_group_id)
            / "current"
            / "current.snapshot"
        )


@dataclass(frozen=True, slots=True)
class CapitalViewFrame:
    key: CapitalViewKey
    generation: int
    applied_event_sequence: int
    payload: bytes
    value: Any


class CapitalViewReader:
    def __init__(
        self, root: str | Path, key: CapitalViewKey, *, retries: int = 8
    ) -> None:
        self.key = key
        self._reader = SharedSnapshotReader(key.resource_path(root), retries=retries)

    @property
    def path(self) -> Path:
        return self._reader.path

    def read(self) -> CapitalViewFrame:
        snapshot = self._reader.read()
        value = decode_view(snapshot.payload)
        metadata = value.Metadata()
        state = value.State()
        if metadata is None or state is None:
            raise ValueError("Capital current view is incomplete")
        if _text(metadata.ViewKey()) != self.key.canonical_key():
            raise ValueError("Capital current view key identity mismatch")
        if _text(state.CapitalGroupId()) != self.key.capital_group_id:
            raise ValueError("Capital current view group identity mismatch")
        return CapitalViewFrame(
            key=self.key,
            generation=snapshot.generation,
            applied_event_sequence=snapshot.applied_event_sequence,
            payload=snapshot.payload,
            value=value,
        )


class CapitalCurrentViewQueries:
    """Read Capital availability without using its JSON control plane."""

    def __init__(
        self, root: str | Path, capital_group_id: str, *, retries: int = 8
    ) -> None:
        self._reader = CapitalViewReader(
            root, CapitalViewKey(capital_group_id), retries=retries
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def read_frame(self) -> CapitalViewFrame:
        return self._reader.read()

    def current(self) -> dict[str, Any]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        availabilities = tuple(
            _availability(state.Availability(index), self._reader.key.capital_group_id)
            for index in range(int(state.AvailabilityLength()))
        )
        alerts = tuple(
            _recovery_alert(state.Alerts(index))
            for index in range(int(state.AlertsLength()))
        )
        return {
            "capital_group_id": self._reader.key.capital_group_id,
            "kind": "current",
            "generation": frame.generation,
            "applied_event_sequence": frame.applied_event_sequence,
            "path": str(self.path),
            "strategy_id": _text(state.StrategyId()),
            "environment": _text(state.Environment()),
            "membership_version": int(state.MembershipVersion()),
            "event_sequence": int(state.EventSequence()),
            "journal_sequence": int(state.JournalSequence()),
            "summary": {
                "objective_count": int(state.ObjectivesLength()),
                "demand_count": int(state.DemandsLength()),
                "policy_count": int(state.PoliciesLength()),
                "facts_count": int(state.FactsLength()),
                "availability_count": len(availabilities),
                "route_count": int(state.RoutesLength()),
                "plan_count": int(state.PlansLength()),
                "reservation_count": int(state.ReservationsLength()),
                "operation_count": int(state.OperationsLength()),
                "alert_count": len(alerts),
                "ready_availability_count": sum(
                    1
                    for value in availabilities
                    if value.readiness is CapitalReadiness.READY
                ),
                "degraded_availability_count": sum(
                    1
                    for value in availabilities
                    if value.readiness is CapitalReadiness.DEGRADED
                ),
                "critical_alert_count": sum(
                    1
                    for value in alerts
                    if value.severity is CapitalAlertSeverity.CRITICAL
                ),
            },
            "availabilities": [asdict(value) for value in availabilities],
            "alerts": [asdict(value) for value in alerts],
        }

    def availabilities(self) -> tuple[CapitalAvailability, ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _availability(state.Availability(index), self._reader.key.capital_group_id)
            for index in range(int(state.AvailabilityLength()))
        )

    def objectives(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _objective(state.Objectives(index))
            for index in range(int(state.ObjectivesLength()))
        )

    def demands(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _demand(state.Demands(index))
            for index in range(int(state.DemandsLength()))
        )

    def plans(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _plan(state.Plans(index)) for index in range(int(state.PlansLength()))
        )

    def routes(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _route(state.Routes(index)) for index in range(int(state.RoutesLength()))
        )

    def reservations(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _reservation(state.Reservations(index))
            for index in range(int(state.ReservationsLength()))
        )

    def operations(self) -> tuple[dict[str, Any], ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _operation(state.Operations(index))
            for index in range(int(state.OperationsLength()))
        )

    def alerts(self) -> tuple[CapitalRecoveryAlert, ...]:
        frame = self.read_frame()
        state = cast(Any, frame.value.State())
        return tuple(
            _recovery_alert(state.Alerts(index))
            for index in range(int(state.AlertsLength()))
        )

    def availability(
        self,
        *,
        capital_group_id: str | None,
        location: FundingLocation | None,
    ) -> CapitalAvailability:
        if capital_group_id != self._reader.key.capital_group_id:
            raise ValueError("Capital current view belongs to another capital group")
        values = self.availabilities()
        if location is None:
            if len(values) != 1:
                raise ValueError(
                    "Capital location is required when the group has multiple locations"
                )
            return values[0]
        for value in values:
            if value.location == location:
                return value
        raise LookupError("Capital location has not been evaluated")


def decode_view(payload: bytes) -> Any:
    from kairospy.infrastructure.transport.generated.kairos.capital.v2.CapitalCurrentView import (
        CapitalCurrentView,
    )

    if not CapitalCurrentView.CapitalCurrentViewBufferHasIdentifier(payload, 0):
        raise ValueError("invalid Capital current view identifier: expected b'CPV2'")
    return CapitalCurrentView.GetRootAs(payload, 0)


def _availability(value: object | None, capital_group_id: str) -> CapitalAvailability:
    if value is None:
        raise ValueError("Capital view contains an empty availability entry")
    row = cast(Any, value)
    location = row.Destination()
    if location is None:
        raise ValueError("Capital availability destination is missing")
    readiness = {
        0: CapitalReadiness.WAITING_FOR_FACTS,
        1: CapitalReadiness.DEGRADED,
        2: CapitalReadiness.READY,
        3: CapitalReadiness.WAITING_FOR_ACCOUNTS,
    }.get(int(row.Readiness()))
    if readiness is None:
        raise ValueError(f"unknown Capital readiness: {row.Readiness()}")
    return CapitalAvailability(
        capital_group_id=capital_group_id,
        readiness=readiness,
        location=FundingLocation(
            broker=_required_text(location.Broker(), "broker"),
            account_id=AccountId(_required_text(location.AccountId(), "account_id")),
            segment=SegmentKey(_required_text(location.Segment(), "segment")),
            asset=_required_text(location.Asset(), "asset"),
        ),
        policy_version=int(row.PolicyVersion()),
        active_objective_ids=_strings(row, "ActiveObjectiveIds"),
        active_demand_ids=_strings(row, "ActiveDemandIds"),
        funding_horizons=tuple(
            _funding_horizon(row.FundingHorizons(index))
            for index in range(int(row.FundingHorizonsLength()))
        ),
        desired_target=_decimal(row.DesiredTarget()),
        observed_available=_decimal(row.ObservedAvailable()),
        effective_target=_decimal(row.EffectiveTarget()),
        deficit=_decimal(row.Deficit()),
        account_watermark=int(row.AccountWatermark()),
        risk_policy_version=int(row.RiskPolicyVersion()),
        risk_watermark=int(row.RiskWatermark()),
        reason=_text(row.Reason()),
    )


def _funding_horizon(value: object | None) -> CapitalFundingHorizon:
    if value is None:
        raise ValueError("Capital view contains an empty funding horizon")
    row = cast(Any, value)
    return CapitalFundingHorizon(
        required_by=datetime.fromtimestamp(
            int(row.RequiredByUnixNanos()) / 1_000_000_000,
            tz=timezone.utc,
        ),
        objective_ids=_strings(row, "ObjectiveIds"),
        demand_ids=_strings(row, "DemandIds"),
        desired_available=_decimal(row.DesiredAvailable()),
    )


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


def _recovery_alert(value: object | None) -> CapitalRecoveryAlert:
    if value is None:
        raise ValueError("Capital view contains an empty recovery alert")
    row = cast(Any, value)
    kind = {
        0: CapitalAlertKind.RECONCILIATION_REQUIRED,
        1: CapitalAlertKind.MANUAL_REVIEW,
    }.get(int(row.Kind()))
    severity = {
        0: CapitalAlertSeverity.WARNING,
        1: CapitalAlertSeverity.CRITICAL,
    }.get(int(row.Severity()))
    recovery_action = {
        2: CapitalRecoveryAction.RECONCILE_ORIGINAL_OPERATION,
        3: CapitalRecoveryAction.HOLD_AND_REVIEW,
    }.get(int(row.RecoveryAction()))
    if kind is None or severity is None or recovery_action is None:
        raise ValueError("Capital recovery alert contains an unknown enum value")
    return CapitalRecoveryAlert(
        alert_id=_required_text(row.AlertId(), "alert_id"),
        plan_id=_required_text(row.PlanId(), "plan_id"),
        operation_id=_text(row.OperationId()),
        kind=kind,
        severity=severity,
        recovery_action=recovery_action,
        message=_required_text(row.Message(), "message"),
        opened_at=datetime.fromtimestamp(
            int(row.OpenedAtUnixNanos()) / 1_000_000_000,
            tz=timezone.utc,
        ),
    )


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
    "CapitalCurrentViewQueries",
    "CapitalViewFrame",
    "CapitalViewKey",
    "CapitalViewReader",
    "decode_view",
]
