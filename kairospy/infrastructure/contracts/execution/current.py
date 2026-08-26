"""Application queries over Execution v2 current-view roots."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .view import ExecutionViewKey, ExecutionViewKind, ExecutionViewReader


class ExecutionCurrentViews:
    """Read-only application queries backed only by v2 current views."""

    def __init__(self, instance: object, *, retries: int = 8) -> None:
        self._root = getattr(instance, "root")
        self._workspace_id = getattr(getattr(instance, "workspace"), "workspace_id")
        self._launch_id = getattr(instance, "launch_id")
        self._instance_id = getattr(instance, "instance_id")
        self._retries = retries

    def orders(self) -> tuple[dict[str, object], ...]:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        return tuple(
            _order(value.Orders(index)) for index in range(value.OrdersLength())
        )

    def get_order(self, order_id: str) -> dict[str, object] | None:
        return next(
            (value for value in self.orders() if value["order_id"] == order_id), None
        )

    def commitments(self) -> tuple[dict[str, object], ...]:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        return tuple(
            _commitment(value.Commitments(index))
            for index in range(value.CommitmentsLength())
        )

    def risk_reservations(self) -> tuple[dict[str, object], ...]:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        return tuple(
            _risk_reservation(value.RiskReservations(index))
            for index in range(value.RiskReservationsLength())
        )

    def open_orders(
        self, *, account_id: str | None = None
    ) -> tuple[dict[str, object], ...]:
        terminal = {
            "filled",
            "canceled",
            "rejected",
            "expired",
            "failed",
        }
        return tuple(
            value
            for value in self.orders()
            if value["status"] not in terminal
            and (account_id is None or value["account_id"] == account_id)
        )

    def intents(self) -> tuple[dict[str, object], ...]:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        return tuple(
            _intent(value.Intents(index)) for index in range(value.IntentsLength())
        )

    def get_intent(self, intent_id: str) -> dict[str, object] | None:
        return next(
            (value for value in self.intents() if value["intent_id"] == intent_id), None
        )

    def recovery_snapshot(
        self,
    ) -> tuple[
        int,
        tuple[dict[str, object], ...],
        tuple[dict[str, object], ...],
        bool,
    ]:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        metadata = value.Metadata()
        if metadata is None:
            raise ValueError("Execution current view metadata is missing")
        return (
            int(metadata.AppliedRevision() or 0),
            tuple(
                _intent(value.Intents(index)) for index in range(value.IntentsLength())
            ),
            tuple(_fill(value.Fills(index)) for index in range(value.FillsLength())),
            bool(value.FillHistoryTruncated()),
        )

    def diagnostic_intent(self, intent_id: str) -> dict[str, object] | None:
        value = self._read(ExecutionViewKind.CURRENT_EXECUTION)
        state = None
        for index in range(value.IntentsLength()):
            candidate = value.Intents(index)
            candidate_intent = candidate.Intent()
            if (
                candidate_intent is not None
                and _text(candidate_intent.IntentId()) == intent_id
            ):
                state = candidate
                break
        if state is None:
            return None
        intent = _intent(state)
        plan = state.Plan()
        metadata = value.Metadata()
        return {
            "intent": {
                **intent,
            },
            "plan": None if plan is None else _diagnostic_plan(plan),
            "orders": [
                _diagnostic_order(value.Orders(index))
                for index in range(value.OrdersLength())
                if _text(value.Orders(index).IntentId()) == intent_id
            ],
            "fills": [
                _diagnostic_fill(value.Fills(index))
                for index in range(value.FillsLength())
                if _text(value.Fills(index).IntentId()) == intent_id
            ],
            "lifecycle_transitions": [
                _diagnostic_intent_event(value.IntentEvents(index))
                for index in range(value.IntentEventsLength())
                if _text(value.IntentEvents(index).IntentId()) == intent_id
            ],
            "order_transitions": [
                _diagnostic_order_event(value.OrderEvents(index))
                for index in range(value.OrderEventsLength())
                if _text(value.OrderEvents(index).IntentId()) == intent_id
            ],
            "view": {
                "applied_event_sequence": (
                    None if metadata is None else int(metadata.AppliedRevision() or 0)
                ),
                "fill_history_truncated": bool(value.FillHistoryTruncated()),
                "order_event_history_truncated": bool(
                    value.OrderEventHistoryTruncated()
                ),
                "intent_event_history_truncated": bool(
                    value.IntentEventHistoryTruncated()
                ),
            },
        }

    def _read(self, kind: ExecutionViewKind) -> Any:
        key = ExecutionViewKey(
            workspace_id=self._workspace_id,
            launch_id=self._launch_id,
            instance_id=self._instance_id,
            kind=kind,
        )
        return ExecutionViewReader(self._root, key, retries=self._retries).read().value


def _order(value: Any) -> dict[str, object]:
    instrument_id = _required_text(value.InstrumentId(), "order instrument_id")
    status = _order_status(int(value.Lifecycle()))
    return {
        "order_id": _required_text(value.OrderId(), "order_id"),
        "strategy_id": _required_text(value.StrategyId(), "order strategy_id"),
        "intent_id": _optional_text(value.IntentId()),
        "instrument_id": instrument_id,
        "account_id": _required_text(value.AccountId(), "order account_id"),
        "side": "buy" if int(value.Side()) == 1 else "sell",
        "quantity": _required_decimal_text(value.Quantity(), "order quantity"),
        "filled_quantity": _required_decimal_text(
            value.FilledQuantity(), "order filled_quantity"
        ),
        "limit_price": _decimal_text(value.LimitPrice()),
        "status": status,
        "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
    }


def _intent(value: Any) -> dict[str, object]:
    intent = value.Intent()
    if intent is None:
        raise ValueError("Execution intent state payload is missing")
    if intent.LegsLength() == 0:
        raise ValueError("Execution intent requires at least one leg")
    instrument_id = _required_text(
        intent.Legs(0).InstrumentId(), "intent instrument_id"
    )
    accounts = tuple(
        _required_text(intent.Legs(index).AccountId(), "intent account_id")
        for index in range(intent.LegsLength())
    )
    plan = value.Plan()
    decoded_order_ids: list[str] = []
    if plan is not None:
        for leg_index in range(plan.LegsLength()):
            leg = plan.Legs(leg_index)
            if leg is None:
                continue
            decoded_order_ids.extend(
                _required_text(leg.OrderIds(index), "intent order_id")
                for index in range(leg.OrderIdsLength())
            )
    order_ids = tuple(dict.fromkeys(decoded_order_ids))
    first_leg = intent.Legs(0)
    return {
        "intent_id": _required_text(intent.IntentId(), "intent_id"),
        "strategy_id": _required_text(intent.StrategyId(), "intent strategy_id"),
        "instrument_id": instrument_id,
        "account_ids": list(accounts),
        "target_quantity": _required_decimal_text(
            first_leg.Quantity(), "intent target quantity"
        ),
        "status": _intent_status(int(value.Lifecycle())),
        "reason": _text(value.Reason()) or "",
        "order_ids": list(order_ids),
        "strategy_decision_id": _text(intent.StrategyDecisionId()),
        "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
    }


def _diagnostic_plan(value: Any) -> dict[str, object]:
    return {
        "plan_id": _required_text(value.PlanId(), "plan_id"),
        "intent_id": _required_text(value.IntentId(), "plan intent_id"),
        "intent_type": int(value.IntentType()),
        "completion_policy": int(value.CompletionPolicy()),
        "failure_policy": int(value.FailurePolicy()),
        "created_at_unix_nanos": int(value.CreatedAtUnixNanos()),
        "legs": [
            {
                "leg_id": _required_text(value.Legs(index).LegId(), "plan leg_id"),
                "account_id": _required_text(
                    value.Legs(index).AccountId(), "plan leg account_id"
                ),
                "instrument_id": _required_text(
                    value.Legs(index).InstrumentId(), "plan leg instrument_id"
                ),
                "market_id": _text(value.Legs(index).MarketId()),
                "lifecycle": int(value.Legs(index).Lifecycle()),
                "order_ids": [
                    _required_text(
                        value.Legs(index).OrderIds(order_index), "plan order_id"
                    )
                    for order_index in range(value.Legs(index).OrderIdsLength())
                ],
                "completed_quantity": _decimal_text(
                    value.Legs(index).CompletedQuantity()
                ),
            }
            for index in range(value.LegsLength())
        ],
    }


def _diagnostic_order(value: Any) -> dict[str, object]:
    return {
        "order_id": _required_text(value.OrderId(), "order_id"),
        "plan_id": _text(value.PlanId()),
        "leg_id": _text(value.LegId()),
        "account_id": _required_text(value.AccountId(), "order account_id"),
        "instrument_id": _required_text(value.InstrumentId(), "order instrument_id"),
        "lifecycle": _order_status(int(value.Lifecycle())),
        "quantity": _decimal_text(value.Quantity()),
        "filled_quantity": _decimal_text(value.FilledQuantity()),
        "average_fill_price": _decimal_text(value.AverageFillPrice()),
        "remote_order_id": _text(value.RemoteOrderId()),
        "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
        "reason": _text(value.Reason()) or "",
    }


def _diagnostic_fill(value: Any) -> dict[str, object]:
    return {
        "fill_id": _required_text(value.FillId(), "fill_id"),
        "order_id": _required_text(value.OrderId(), "fill order_id"),
        "plan_id": _text(value.PlanId()),
        "leg_id": _text(value.LegId()),
        "account_id": _required_text(value.AccountId(), "fill account_id"),
        "instrument_id": _required_text(value.InstrumentId(), "fill instrument_id"),
        "quantity": _decimal_text(value.Quantity()),
        "price": _decimal_text(value.Price()),
        "fee": _decimal_text(value.Fee()),
        "fee_asset_id": _text(value.FeeAssetId()),
        "source_filled_at_unix_nanos": int(value.SourceFilledAtUnixNanos()),
    }


def _fill(value: Any) -> dict[str, object]:
    instrument_id = _required_text(value.InstrumentId(), "fill instrument_id")
    occurred_at_unix_nanos = int(value.SourceFilledAtUnixNanos())
    return {
        "fill_id": _required_text(value.FillId(), "fill_id"),
        "order_id": _required_text(value.OrderId(), "fill order_id"),
        "instrument_id": instrument_id,
        "quantity": _required_decimal_text(value.Quantity(), "fill quantity"),
        "price": _required_decimal_text(value.Price(), "fill price"),
        "occurred_at_unix_nanos": occurred_at_unix_nanos,
        "intent_id": _optional_text(value.IntentId()),
    }


def _diagnostic_intent_event(value: Any) -> dict[str, object]:
    return {
        "event_sequence": int(value.EventSequence()),
        "previous_status": _intent_status(int(value.PreviousLifecycle())),
        "status": _intent_status(int(value.Lifecycle())),
        "order_ids": [
            _required_text(value.OrderIds(index), "intent event order_id")
            for index in range(value.OrderIdsLength())
        ],
        "completed_quantity": _decimal_text(value.CompletedQuantity()),
        "occurred_at_unix_nanos": int(value.OccurredAtUnixNanos()),
        "reason": _text(value.Reason()) or "",
    }


def _diagnostic_order_event(value: Any) -> dict[str, object]:
    return {
        "order_id": _required_text(value.OrderId(), "order event order_id"),
        "plan_id": _text(value.PlanId()),
        "leg_id": _text(value.LegId()),
        "status": _order_status(int(value.Lifecycle())),
        "remote_order_id": _text(value.RemoteOrderId()),
        "occurred_at_unix_nanos": int(value.OccurredAtUnixNanos()),
        "reason": _text(value.Reason()) or "",
        "fill_id": _text(value.FillId()),
        "filled_quantity": _decimal_text(value.FilledQuantity()),
    }


def _commitment(value: Any) -> dict[str, object]:
    return {
        "order_id": _required_text(value.OrderId(), "commitment order_id"),
        "account_id": _required_text(value.AccountId(), "commitment account_id"),
        "segment_key": _required_text(value.SegmentKey(), "commitment segment_key"),
        "instrument_id": _required_text(
            value.InstrumentId(), "commitment instrument_id"
        ),
        "resource_kind": {1: "asset", 2: "instrument", 3: "margin_notional"}.get(
            int(value.ResourceKind()), "unspecified"
        ),
        "resource_id": _required_text(value.ResourceId(), "commitment resource_id"),
        "amount": _required_decimal_text(value.Amount(), "commitment amount"),
        "remaining_quantity": _required_decimal_text(
            value.RemainingQuantity(), "commitment remaining_quantity"
        ),
        "status": {
            1: "held_before_send",
            2: "active",
            3: "uncertain",
            4: "reduced",
            5: "released",
            6: "reconciled",
        }.get(int(value.Lifecycle()), "uncertain"),
        "basis_kind": {
            1: "quote_price_cap",
            2: "base_quantity",
            3: "contract_notional",
            4: "simulation_quantity",
        }.get(int(value.BasisKind()), "unspecified"),
        "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
    }


def _risk_reservation(value: Any) -> dict[str, object]:
    raw_funding = value.FundingRequirement()
    return {
        "order_id": _required_text(value.OrderId(), "reservation order_id"),
        "reservation_id": _required_text(
            value.ReservationId(), "reservation reservation_id"
        ),
        "idempotency_key": _required_text(
            value.IdempotencyKey(), "reservation idempotency_key"
        ),
        "account_id": _required_text(value.AccountId(), "reservation account_id"),
        "amount": _required_decimal_text(value.Amount(), "reservation amount"),
        "status": {
            1: "authorize_pending",
            2: "active",
            3: "resize_pending",
            4: "release_pending",
            5: "consume_pending",
            6: "released",
            7: "consumed",
            8: "expired",
            9: "uncertain",
            10: "failed",
        }.get(int(value.Lifecycle()), "uncertain"),
        "risk_generation": int(value.RiskGeneration()),
        "risk_event_sequence": int(value.RiskEventSequence()),
        "policy_version": int(value.PolicyVersion()),
        "expires_at_unix_nanos": int(value.ExpiresAtUnixNanos()),
        "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
        "funding_requirement": (
            None
            if raw_funding is None
            else {
                "required_margin": _required_decimal_text(
                    raw_funding.RequiredMargin(), "funding required_margin"
                ),
                "available_margin": _required_decimal_text(
                    raw_funding.AvailableMargin(), "funding available_margin"
                ),
                "shortfall": _required_decimal_text(
                    raw_funding.Shortfall(), "funding shortfall"
                ),
                "margin_rule_id": _required_text(
                    raw_funding.MarginRuleId(), "funding margin_rule_id"
                ),
                "risk_decision_id": _required_text(
                    raw_funding.RiskDecisionId(), "funding risk_decision_id"
                ),
                "risk_policy_version": int(raw_funding.RiskPolicyVersion()),
                "account_snapshot_watermark": int(
                    raw_funding.AccountSnapshotWatermark()
                ),
                "broker": _required_text(raw_funding.Broker(), "funding broker"),
                "segment": _required_text(raw_funding.Segment(), "funding segment"),
                "collateral_asset": _required_text(
                    raw_funding.CollateralAsset(), "funding collateral_asset"
                ),
            }
        ),
    }


def _order_status(value: int) -> str:
    return {
        1: "pending",
        2: "submitting",
        3: "accepted",
        4: "partially_filled",
        5: "filled",
        6: "cancel_requested",
        7: "canceled",
        8: "rejected",
        9: "expired",
        11: "failed",
    }.get(value, "unknown")


def _intent_status(value: int) -> str:
    names = {
        1: "accepted",
        2: "planning",
        3: "planned",
        4: "executing",
        5: "partially_filled",
        6: "cancel_requested",
        7: "satisfied",
        8: "rejected",
        9: "canceled",
        10: "expired",
        11: "failed",
        12: "compensating",
        13: "reconciliation_required",
    }
    return names.get(value, "unknown")


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = value
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))  # type: ignore[attr-defined]


def _required_decimal_text(value: object | None, name: str) -> str:
    result = _decimal(value)
    if result is None:
        raise ValueError(f"{name} is required")
    return format(result, "f")


def _decimal_text(value: object | None) -> str | None:
    result = _decimal(value)
    return None if result is None else format(result, "f")


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def _required_text(value: bytes | None, name: str) -> str:
    result = _text(value)
    if result is None or not result.strip():
        raise ValueError(f"{name} is required")
    return result


def _optional_text(value: bytes | None) -> str | None:
    raw = _text(value)
    return None if raw is None or not raw.strip() else raw


__all__ = ["ExecutionCurrentViews"]
