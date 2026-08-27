"""Application queries over Execution owner-contract current views."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .view import ExecutionIndexedViewReader


class ExecutionCurrentViews:
    """Read-only application queries backed only by the native owner contract."""

    def __init__(self, instance: object) -> None:
        snapshot = getattr(instance, "snapshot")
        self._reader = ExecutionIndexedViewReader(
            snapshot(),
            workspace_id=getattr(getattr(instance, "workspace"), "workspace_id"),
            launch_id=getattr(instance, "launch_id"),
            instance_id=getattr(instance, "instance_id"),
        )

    def orders(self) -> tuple[dict[str, object], ...]:
        return tuple(_order(value) for value in self._reader.orders())

    def get_order(self, order_id: str) -> dict[str, object] | None:
        value = self._reader.get_order(order_id)
        return None if value is None else _order(value)

    def commitments(self) -> tuple[dict[str, object], ...]:
        return tuple(_commitment(value) for value in self._reader.commitments())

    def algorithm_runs(self) -> tuple[dict[str, object], ...]:
        return tuple(_algorithm_run(value) for value in self._reader.algorithm_runs())

    def risk_reservations(self) -> tuple[dict[str, object], ...]:
        return tuple(_risk_reservation(value) for value in self._reader.risk_reservations())

    def unknown_remote_orders(self) -> tuple[dict[str, object], ...]:
        return tuple(
            _unknown_remote_order(value)
            for value in self._reader.unknown_remote_orders()
        )

    def open_orders(self, *, account_id: str | None = None) -> tuple[dict[str, object], ...]:
        terminal = {"filled", "canceled", "rejected", "expired", "failed"}
        return tuple(
            value
            for value in self.orders()
            if value["status"] not in terminal
            and (account_id is None or value["account_id"] == account_id)
        )

    def intents(self) -> tuple[dict[str, object], ...]:
        return tuple(_intent(value) for value in self._reader.intents())

    def get_intent(self, intent_id: str) -> dict[str, object] | None:
        value = self._reader.get_intent(intent_id)
        return None if value is None else _intent(value)

    def close(self) -> None:
        self._reader.close()


def _order(value: Any) -> dict[str, object]:
    return {
        "order_id": value.order_id,
        "strategy_id": value.strategy_id,
        "intent_id": value.intent_id,
        "instrument_id": value.instrument_id,
        "account_id": value.account_id,
        "side": value.side,
        "quantity": _required_decimal_text(value.quantity),
        "filled_quantity": _required_decimal_text(value.filled_quantity),
        "limit_price": _decimal_text(value.limit_price),
        "status": value.status,
        "updated_at_unix_nanos": value.updated_at_unix_nanos,
    }


def _intent(value: Any) -> dict[str, object]:
    return {
        "intent_id": value.intent_id,
        "strategy_id": value.strategy_id,
        "instrument_id": value.instrument_id,
        "account_ids": list(value.account_ids),
        "target_quantity": _required_decimal_text(value.target_quantity),
        "status": value.status,
        "reason": value.reason,
        "order_ids": list(value.order_ids),
        "strategy_decision_id": value.strategy_decision_id,
        "updated_at_unix_nanos": value.updated_at_unix_nanos,
    }


def _commitment(value: Any) -> dict[str, object]:
    return {
        "order_id": value.order_id,
        "account_id": value.account_id,
        "segment_key": value.segment_key,
        "instrument_id": value.instrument_id,
        "resource_kind": value.resource_kind,
        "resource_id": value.resource_id,
        "amount": _required_decimal_text(value.amount),
        "remaining_quantity": _required_decimal_text(value.remaining_quantity),
        "status": value.status,
        "basis_kind": value.basis_kind,
        "updated_at_unix_nanos": value.updated_at_unix_nanos,
    }


def _risk_reservation(value: Any) -> dict[str, object]:
    funding = value.funding_requirement
    return {
        "order_id": value.order_id,
        "reservation_id": value.reservation_id,
        "idempotency_key": value.idempotency_key,
        "account_id": value.account_id,
        "amount": _required_decimal_text(value.amount),
        "status": value.status,
        "risk_generation": value.risk_generation,
        "risk_event_sequence": value.risk_event_sequence,
        "policy_version": value.policy_version,
        "expires_at_unix_nanos": value.expires_at_unix_nanos,
        "updated_at_unix_nanos": value.updated_at_unix_nanos,
        "funding_requirement": None if funding is None else {
            "required_margin": _required_decimal_text(funding.required_margin),
            "available_margin": _required_decimal_text(funding.available_margin),
            "shortfall": _required_decimal_text(funding.shortfall),
            "margin_rule_id": funding.margin_rule_id,
            "risk_decision_id": funding.risk_decision_id,
            "risk_policy_version": funding.risk_policy_version,
            "account_snapshot_watermark": funding.account_snapshot_watermark,
            "broker": funding.broker,
            "segment": funding.segment,
            "collateral_asset": funding.collateral_asset,
        },
    }


def _algorithm_run(value: Any) -> dict[str, object]:
    return {
        "algorithm_run_id": value.algorithm_run_id,
        "algorithm_version": value.algorithm_version,
        "intent_id": value.intent_id,
        "algorithm_kind": value.algorithm_kind,
        "status": value.status,
        "decision_sequence": value.decision_sequence,
        "last_decision_at_unix_nanos": value.last_decision_at_unix_nanos,
        "next_wake_at_unix_nanos": value.next_wake_at_unix_nanos,
        "action_count": value.action_count,
        "pending_action_count": value.pending_action_count,
        "indeterminate_action_count": value.indeterminate_action_count,
        "leg_count": value.leg_count,
    }


def _unknown_remote_order(value: Any) -> dict[str, object]:
    return {
        "remote_order_id": value.remote_order_id,
        "symbol": value.symbol,
        "status": value.status,
        "execution_id": value.execution_id,
        "fill_quantity": _decimal_text(value.fill_quantity),
        "fill_price": _decimal_text(value.fill_price),
        "fee_currency": value.fee_currency,
        "fee_amount": _decimal_text(value.fee_amount),
        "first_seen_at_unix_nanos": value.first_seen_at_unix_nanos,
        "last_seen_at_unix_nanos": value.last_seen_at_unix_nanos,
        "resolution": value.resolution,
        "reason": value.reason,
    }


def _decimal(value: Any | None) -> Decimal | None:
    return None if value is None else Decimal(value.mantissa).scaleb(-value.scale)


def _required_decimal_text(value: Any | None) -> str:
    result = _decimal(value)
    if result is None:
        raise ValueError("Execution native contract omitted a required decimal")
    return format(result, "f")


def _decimal_text(value: Any | None) -> str | None:
    result = _decimal(value)
    return None if result is None else format(result, "f")


__all__ = ["ExecutionCurrentViews"]
