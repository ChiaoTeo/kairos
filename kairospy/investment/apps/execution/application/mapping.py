from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from .models import (
    CommitmentStatus,
    ExecutionFundingRequirement,
    ExecutionBacktestResult,
    ExecutionIntent,
    Fill,
    IntentStatus,
    Order,
    OrderCommitment,
    OrderSide,
    OrderStatus,
    RiskReservationSaga,
    RiskReservationSagaStatus,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.investment.application.eventing import EventMetadata
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.primitives.time import datetime_from_unix_nanos
from .events import (
    ExecutionEvent,
    ExecutionEventRecord,
    FillEvent,
    IntentUpdateEvent,
    OrderUpdateEvent,
)
from kairospy.investment.apps.market.application import BarEvent, QuoteEvent


def backtest_market_payload(event: object) -> dict[str, object] | None:
    """Map a strategy-visible Market event into an Execution contract payload."""

    if isinstance(event, QuoteEvent):
        quote = event.data
        if quote.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped quote; "
                "resolve consolidated data through an Execution destination route first"
            )
        if quote.provider is None:
            raise ValueError("Execution backtest quote requires provider provenance")
        return {
            "Quote": {
                "scope": {"kind": "market", "market_id": str(quote.market_id)},
                "instrument_id": str(quote.instrument.id),
                "bid_price": None
                if quote.bid_price is None
                else format(quote.bid_price, "f"),
                "bid_quantity": None
                if quote.bid_quantity is None
                else format(quote.bid_quantity, "f"),
                "ask_price": None
                if quote.ask_price is None
                else format(quote.ask_price, "f"),
                "ask_quantity": None
                if quote.ask_quantity is None
                else format(quote.ask_quantity, "f"),
                "observed_at_unix_nanos": quote.occurred_at_unix_nanos,
                "provider": quote.provider,
            }
        }
    if isinstance(event, BarEvent):
        bar = event.data
        if bar.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped bar; "
                "resolve consolidated data through an Execution destination route first"
            )
        if bar.provider is None:
            raise ValueError("Execution backtest bar requires provider provenance")
        return {
            "Bar": {
                "scope": {"kind": "market", "market_id": str(bar.market_id)},
                "instrument_id": str(bar.instrument.id),
                "timeframe": bar.timeframe,
                "open": format(bar.open, "f"),
                "high": format(bar.high, "f"),
                "low": format(bar.low, "f"),
                "close": format(bar.close, "f"),
                "volume": None if bar.volume is None else format(bar.volume, "f"),
                "observed_at_unix_nanos": bar.occurred_at_unix_nanos,
                "provider": bar.provider,
                "derivation": "provider",
            }
        }
    return None


def map_execution_event(record: ExecutionEventRecord) -> tuple[ExecutionEvent, ...]:
    metadata = EventMetadata(
        stream_id=record.stream_id,
        sequence=record.sequence,
        producer=record.producer,
        occurred_at_unix_nanos=record.occurred_at_unix_nanos,
    )
    events: list[ExecutionEvent] = []
    for change in record.changes:
        if change.kind == "intent_update":
            events.append(
                IntentUpdateEvent(
                    map_execution_intent(
                        _strategy_payload(change.payload, change.strategy_id)
                    ),
                    metadata,
                    _enum(
                        IntentStatus,
                        _mapping(change.payload, "Execution intent event").get(
                            "previous_status"
                        ),
                        None,
                    ),
                )
            )
        elif change.kind == "order_update":
            events.append(
                OrderUpdateEvent(
                    map_execution_order(
                        _strategy_payload(change.payload, change.strategy_id)
                    ),
                    metadata,
                )
            )
        elif change.kind == "fill":
            events.append(FillEvent(map_execution_fill(change.payload), metadata))
        else:
            raise ValueError(f"unsupported Execution event kind: {change.kind}")
    return tuple(events)


def map_execution_intent(value: object) -> ExecutionIntent:
    root = _mapping(value, "Execution intent")
    intent_value = root.get("intent", root)
    intent = _mapping(intent_value, "Execution intent payload")
    status = _enum(
        IntentStatus,
        root.get("status", intent.get("status", "unknown")),
        IntentStatus.UNKNOWN,
    )
    instrument_id = _required(intent, "instrument_id")
    source_event_sequence = intent.get("source_event_sequence")
    return ExecutionIntent(
        id=IntentId(_required(intent, "intent_id")),
        strategy_id=_required(root, "strategy_id"),
        instrument=_instrument(instrument_id),
        account_ids=tuple(
            AccountId(str(item))
            for item in _sequence(intent.get("account_ids", ()), "account_ids")
        ),
        target_quantity=_decimal(intent.get("target_quantity")),
        status=status,
        reason=str(intent.get("reason", "")),
        order_ids=tuple(
            OrderId(str(item))
            for item in _sequence(root.get("order_ids", ()), "order_ids")
        ),
        source_event_sequence=(
            source_event_sequence
            if isinstance(source_event_sequence, int) and source_event_sequence > 0
            else None
        ),
        strategy_decision_id=(
            str(intent["strategy_decision_id"])
            if isinstance(intent.get("strategy_decision_id"), str)
            and str(intent["strategy_decision_id"]).strip()
            else None
        ),
    )


def map_execution_order(value: object) -> Order:
    row = _mapping(value, "Execution order")
    instrument_id = _required(row, "instrument_id")
    updated_nanos = row.get("updated_at_unix_nanos")
    updated_at = (
        datetime_from_unix_nanos(updated_nanos)
        if isinstance(updated_nanos, int)
        else None
    )
    raw_intent_id = row.get("intent_id")
    return Order(
        id=OrderId(_required(row, "order_id")),
        strategy_id=_required(row, "strategy_id"),
        intent_id=IntentId(raw_intent_id)
        if isinstance(raw_intent_id, str) and raw_intent_id.strip()
        else None,
        instrument=_instrument(instrument_id),
        account_id=AccountId(_required(row, "account_id")),
        side=_enum(OrderSide, row.get("side"), OrderSide.BUY),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        filled_quantity=_decimal(row.get("filled_quantity")) or Decimal("0"),
        limit_price=_decimal(row.get("limit_price")),
        status=_enum(OrderStatus, row.get("status"), OrderStatus.UNKNOWN),
        updated_at=updated_at,
    )


def map_execution_fill(value: object) -> Fill:
    row = _mapping(value, "Execution fill")
    nanos = row.get("occurred_at_unix_nanos")
    if not isinstance(nanos, int):
        raise ValueError("Execution fill occurred_at_unix_nanos must be an integer")
    instrument_id = _required(row, "instrument_id")
    return Fill(
        id=FillId(_required(row, "fill_id")),
        order_id=OrderId(_required(row, "order_id")),
        instrument=_instrument(instrument_id),
        quantity=_decimal(row.get("quantity")) or Decimal("0"),
        price=_decimal(row.get("price")) or Decimal("0"),
        occurred_at=datetime_from_unix_nanos(nanos),
        intent_id=(
            IntentId(str(row["intent_id"]))
            if isinstance(row.get("intent_id"), str) and str(row["intent_id"]).strip()
            else None
        ),
    )


def map_execution_backtest_result(value: object) -> ExecutionBacktestResult:
    row = _mapping(value, "Execution backtest result")
    fills = row.get("fills", ())
    if isinstance(fills, (str, bytes)) or not isinstance(fills, Sequence):
        raise ValueError("Execution backtest fills must be an array")
    return ExecutionBacktestResult(tuple(map_execution_fill(fill) for fill in fills))


def map_order_commitment(value: object) -> OrderCommitment:
    row = _mapping(value, "Execution order commitment")
    return OrderCommitment(
        order_id=OrderId(_required(row, "order_id")),
        account_id=AccountId(_required(row, "account_id")),
        segment_key=SegmentKey(_required(row, "segment_key")),
        instrument_id=InstrumentId(_required(row, "instrument_id")),
        resource_kind=_required(row, "resource_kind"),
        resource_id=_required(row, "resource_id"),
        amount=_decimal(row.get("amount")) or Decimal("0"),
        remaining_quantity=_decimal(row.get("remaining_quantity")) or Decimal("0"),
        status=CommitmentStatus(str(row.get("status", "uncertain"))),
        basis_kind=_required(row, "basis_kind"),
        updated_at_unix_nanos=_required_int(
            row.get("updated_at_unix_nanos"), "updated_at_unix_nanos"
        ),
    )


def map_risk_reservation(value: object) -> RiskReservationSaga:
    row = _mapping(value, "Execution risk reservation")
    funding = row.get("funding_requirement")
    funding_row = (
        None
        if funding is None
        else _mapping(funding, "Execution funding requirement")
    )
    return RiskReservationSaga(
        order_id=OrderId(_required(row, "order_id")),
        reservation_id=_required(row, "reservation_id"),
        idempotency_key=_required(row, "idempotency_key"),
        account_id=AccountId(_required(row, "account_id")),
        amount=_decimal(row.get("amount")) or Decimal("0"),
        status=RiskReservationSagaStatus(str(row.get("status", "uncertain"))),
        risk_generation=_required_int(row.get("risk_generation"), "risk_generation"),
        risk_event_sequence=_required_int(
            row.get("risk_event_sequence"), "risk_event_sequence"
        ),
        policy_version=_required_int(row.get("policy_version"), "policy_version"),
        expires_at_unix_nanos=_required_int(
            row.get("expires_at_unix_nanos"), "expires_at_unix_nanos"
        ),
        updated_at_unix_nanos=_required_int(
            row.get("updated_at_unix_nanos"), "updated_at_unix_nanos"
        ),
        funding_requirement=(
            None
            if funding_row is None
            else ExecutionFundingRequirement(
                required_margin=_decimal(funding_row.get("required_margin"))
                or Decimal("0"),
                available_margin=_decimal(funding_row.get("available_margin"))
                or Decimal("0"),
                shortfall=_decimal(funding_row.get("shortfall")) or Decimal("0"),
                margin_rule_id=_required(funding_row, "margin_rule_id"),
                risk_decision_id=_required(funding_row, "risk_decision_id"),
                risk_policy_version=_required_int(
                    funding_row.get("risk_policy_version"), "risk_policy_version"
                ),
                account_snapshot_watermark=_required_int(
                    funding_row.get("account_snapshot_watermark"),
                    "account_snapshot_watermark",
                ),
                broker=_required(funding_row, "broker"),
                segment=_required(funding_row, "segment"),
                collateral_asset=_required(funding_row, "collateral_asset"),
            )
        ),
    )


def _instrument(value: str) -> InstrumentRef:
    return InstrumentRef(InstrumentId(value), value.rsplit(":", 1)[-1])


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _strategy_payload(value: object, strategy_id: str) -> dict[str, object]:
    payload = dict(_mapping(value, "Execution event payload"))
    payload["strategy_id"] = strategy_id
    return payload


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _required(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Execution value is missing {name}")
    return raw


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("decimal values must use the canonical string representation")
    return Decimal(value)


def _required_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Execution {name} must be an integer")
    return value


def _enum(enum_type, value: object, default):
    normalized = str(value or "").replace("_", "").lower()
    for member in enum_type:
        if member.value.replace("_", "").lower() == normalized:
            return member
    return default
