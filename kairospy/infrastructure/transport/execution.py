from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

from kairospy.application.execution.events import (
    ExecutionChangeRecord,
    ExecutionEventRecord,
)
from kairospy.infrastructure.contracts.execution import decode_event
from kairospy.infrastructure.transport.native_event import NativeEventSource
from kairospy.infrastructure.transport.generated_spec import (
    DEFAULT_CHANNEL,
    EXECUTION_EVENTS,
)
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class AeronExecutionEventSource(NativeEventSource[ExecutionEventRecord]):
    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = DEFAULT_CHANNEL,
        stream_id: int = EXECUTION_EVENTS,
    ) -> None:
        super().__init__(
            decoder=decode_execution_event,
            aeron_dir=aeron_dir, channel=channel, stream_id=stream_id,
        )


def decode_execution_event(payload: bytes) -> ExecutionEventRecord:
    if len(payload) < 8 or payload[4:8] not in _V2_EVENT_ROOTS:
        raise ValueError("invalid Execution v2 event identifier")
    return _decode_v2_event(payload)


_V2_EVENT_ROOTS = {
    b"EIA2": "IntentAccepted",
    b"EIR2": "IntentRejected",
    b"EPV2": "PlanCreated",
    b"EOS2": "OrderSubmitted",
    b"EOA2": "OrderAccepted",
    b"EOR2": "OrderRejected",
    b"EOC2": "OrderCanceled",
    b"EOX2": "OrderExpired",
    b"EFV2": "FillRecorded",
    b"EXV2": "ReconciliationRequired",
}


def _decode_v2_event(payload: bytes) -> ExecutionEventRecord:
    root_name = _V2_EVENT_ROOTS[payload[4:8]]
    root = cast(Any, decode_event(payload))
    metadata = cast(Any, root.Metadata())
    if metadata is None:
        raise ValueError("Execution v2 event metadata is missing")
    kind, payload_value = _v2_payload(root_name, root)
    strategy_id = _v2_strategy_id(payload_value)
    account_id = _v2_account_id(payload_value)
    return ExecutionEventRecord(
        stream_id=_required_text(metadata.StreamId(), "stream_id"),
        sequence=int(metadata.Sequence()),
        producer=_required_text(metadata.ProducerId(), "producer_id"),
        instance_id=_text(metadata.InstanceId()),
        changes=(
            ()
            if kind is None
            else (ExecutionChangeRecord(kind, strategy_id, account_id, payload_value),)
        ),
        occurred_at_unix_nanos=int(metadata.OccurredAtUnixNanos()),
        launch_id=_text(metadata.LaunchId()),
    )


def _v2_payload(root_name: str, root: Any) -> tuple[str | None, dict[str, object]]:
    if root_name in {"OrderSubmitted", "OrderAccepted", "OrderRejected", "OrderCanceled", "OrderExpired"}:
        value = root.Order()
        if value is None:
            raise ValueError(f"{root_name} order payload is missing")
        order = _v2_order(value)
        order["status"] = _order_status(int(value.Lifecycle()))
        return "order_update", order
    if root_name == "FillRecorded":
        value = root.Fill()
        if value is None:
            raise ValueError("FillRecorded fill payload is missing")
        fill = _v2_fill(value)
        fill["occurred_at_unix_nanos"] = int(root.Metadata().OccurredAtUnixNanos())
        return "fill", fill
    if root_name == "PlanCreated":
        value = root.Plan()
        if value is None:
            raise ValueError("PlanCreated plan payload is missing")
        return None, {"plan_id": _text(value.PlanId()), "intent_id": _text(value.IntentId())}
    if root_name == "ReconciliationRequired":
        return None, {
            "reconciliation_id": _required_text(root.ReconciliationId(), "reconciliation_id"),
            "reason": int(root.Reason()),
            "intent_id": _text(root.IntentId()),
            "plan_id": _text(root.PlanId()),
            "leg_id": _text(root.LegId()),
            "order_id": _text(root.OrderId()),
            "account_id": _text(root.AccountId()),
            "details": _text(root.Details()) or "",
            "kind": root_name,
        }
    return None, {"intent_id": _required_text(root.IntentId(), "intent_id")}


def _v2_order(value: Any) -> dict[str, object]:
    return {
        "order_id": _required_text(value.OrderId(), "order_id"),
        "intent_id": _required_text(value.IntentId(), "intent_id"),
        "plan_id": _required_text(value.PlanId(), "plan_id"),
        "leg_id": _required_text(value.LegId(), "leg_id"),
        "strategy_id": _required_text(value.StrategyId(), "strategy_id"),
        "account_id": _required_text(value.AccountId(), "account_id"),
        "instrument_id": _required_text(value.InstrumentId(), "instrument_id"),
        "market_id": _required_text(value.MarketId(), "market_id"),
        "execution_access_id": _required_text(value.ExecutionAccessId(), "execution_access_id"),
        "side": _side(value.Side()),
        "quantity": _decimal(value.Quantity()),
        "filled_quantity": _decimal(value.FilledQuantity()),
        "lifecycle": int(value.Lifecycle()),
        "reason": _text(value.Reason()) or "",
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


def _v2_fill(value: Any) -> dict[str, object]:
    return {
        "fill_id": _required_text(value.FillId(), "fill_id"),
        "trade_id": _text(value.TradeId()),
        "order_id": _required_text(value.OrderId(), "order_id"),
        "intent_id": _required_text(value.IntentId(), "intent_id"),
        "plan_id": _required_text(value.PlanId(), "plan_id"),
        "leg_id": _required_text(value.LegId(), "leg_id"),
        "strategy_id": _required_text(value.StrategyId(), "strategy_id"),
        "account_id": _required_text(value.AccountId(), "account_id"),
        "instrument_id": _required_text(value.InstrumentId(), "instrument_id"),
        "quantity": _decimal(value.Quantity()),
        "price": _decimal(value.Price()),
    }


def _v2_strategy_id(value: object) -> str:
    if isinstance(value, dict):
        raw = value.get("strategy_id")
        if isinstance(raw, str) and raw:
            return raw
        nested = value.get("order") or value.get("fill")
        if isinstance(nested, dict) and isinstance(nested.get("strategy_id"), str):
            return cast(str, nested["strategy_id"])
    return "execution"


def _v2_account_id(value: object) -> str | None:
    if isinstance(value, dict):
        raw = value.get("account_id")
        if isinstance(raw, str):
            return raw
        nested = value.get("order") or value.get("fill")
        if isinstance(nested, dict):
            return cast(str | None, nested.get("account_id"))
    return None


def _side(value: int) -> str:
    if value == 1:
        return "buy"
    if value == 2:
        return "sell"
    raise ValueError("Execution event side is unspecified")


def _decimal(value: object | None) -> str | None:
    if value is None:
        return None
    mantissa = int(getattr(value, "Mantissa")())
    scale = int(getattr(value, "Scale")())
    sign = "-" if mantissa < 0 else ""
    digits = str(abs(mantissa)).rjust(scale + 1, "0")
    return (
        f"{sign}{digits}"
        if scale == 0
        else f"{sign}{digits[:-scale]}.{digits[-scale:]}"
    )


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def _required_text(value: bytes | None, name: str) -> str:
    result = _text(value) or ""
    if not result.strip():
        raise ValueError(f"Execution event {name} is required")
    return result


__all__ = ["AeronExecutionEventSource", "decode_execution_event"]
