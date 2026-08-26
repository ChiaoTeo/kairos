"""Application queries over Execution v2 current-view roots."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .view import (
    COMMITMENTS_DATABASE,
    INTENTS_DATABASE,
    ORDERS_DATABASE,
    RISK_RESERVATIONS_DATABASE,
    ExecutionIndexedViewReader,
)


class ExecutionCurrentViews:
    """Read-only application queries backed only by v2 current views."""

    def __init__(self, instance: object) -> None:
        snapshot = getattr(instance, "snapshot")
        self._workspace_id = getattr(getattr(instance, "workspace"), "workspace_id")
        self._launch_id = getattr(instance, "launch_id")
        self._instance_id = getattr(instance, "instance_id")
        self._reader = ExecutionIndexedViewReader(
            snapshot(),
            workspace_id=self._workspace_id,
            launch_id=self._launch_id,
            instance_id=self._instance_id,
        )

    def orders(self) -> tuple[dict[str, object], ...]:
        return tuple(_order(_state(value, "order")) for value in self._reader.values(ORDERS_DATABASE))

    def get_order(self, order_id: str) -> dict[str, object] | None:
        value = self._reader.get(ORDERS_DATABASE, order_id)
        return None if value is None else _order(_state(value, "order"))

    def commitments(self) -> tuple[dict[str, object], ...]:
        return tuple(
            _commitment(_state(value, "commitment"))
            for value in self._reader.values(COMMITMENTS_DATABASE)
        )

    def risk_reservations(self) -> tuple[dict[str, object], ...]:
        return tuple(
            _risk_reservation(_state(value, "risk reservation"))
            for value in self._reader.values(RISK_RESERVATIONS_DATABASE)
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
        return tuple(
            _intent(_state(value, "intent"))
            for value in self._reader.values(INTENTS_DATABASE)
        )

    def get_intent(self, intent_id: str) -> dict[str, object] | None:
        value = self._reader.get(INTENTS_DATABASE, intent_id)
        return None if value is None else _intent(_state(value, "intent"))

    def close(self) -> None:
        self._reader.close()


def _state(value: Any, name: str) -> Any:
    state = value.State()
    if state is None:
        raise ValueError(f"Execution indexed {name} value has no state")
    return state


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
