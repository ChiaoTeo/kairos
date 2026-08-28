from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast

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
from kairospy.primitives.account import AccountId, SegmentKey
from kairospy.primitives.execution import FillId, IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.primitives.decimal import (
    DecimalValue,
    Money,
    MoneyLike,
    Price,
    PriceLike,
    Quantity,
    QuantityLike,
)
from kairospy.primitives.time import datetime_from_unix_nanos
from kairospy.infrastructure.contracts.market.events import MarketEvent
from kairospy.infrastructure.contracts.execution.types import (
    ExecutionBacktestMarketRequest,
)


class _MarketScope(Protocol):
    market_id: str | None


class _MarketObservation(Protocol):
    scope: _MarketScope
    instrument_id: str
    provider: str
    source_observed_at_unix_nanos: int


class _MarketQuote(_MarketObservation, Protocol):
    bid_price: PriceLike | None
    bid_quantity: QuantityLike | None
    ask_price: PriceLike | None
    ask_quantity: QuantityLike | None


class _MarketBar(_MarketObservation, Protocol):
    bar_spec_id: str
    open: PriceLike
    high: PriceLike
    low: PriceLike
    close: PriceLike
    volume: QuantityLike | None


def backtest_market_request(event: object) -> ExecutionBacktestMarketRequest | None:
    """Map one owner-native Market fact into a typed Execution request."""

    if isinstance(event, MarketEvent) and event.kind == "quote":
        quote = cast(_MarketQuote, event.data)
        if quote.scope.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped quote; "
                "resolve consolidated data through an Execution destination route first"
            )
        if quote.provider is None:
            raise ValueError("Execution backtest quote requires provider provenance")
        return ExecutionBacktestMarketRequest.quote(
                market_id=str(quote.scope.market_id),
                instrument_id=str(quote.instrument_id),
                bid_price=None
                if quote.bid_price is None
                else format(_market_decimal(quote.bid_price), "f"),
                bid_quantity=None
                if quote.bid_quantity is None
                else format(_market_decimal(quote.bid_quantity), "f"),
                ask_price=None
                if quote.ask_price is None
                else format(_market_decimal(quote.ask_price), "f"),
                ask_quantity=None
                if quote.ask_quantity is None
                else format(_market_decimal(quote.ask_quantity), "f"),
                observed_at_unix_nanos=quote.source_observed_at_unix_nanos,
                source_id=quote.provider,
        )
    if isinstance(event, MarketEvent) and event.kind == "bar":
        bar = cast(_MarketBar, event.data)
        if bar.scope.market_id is None:
            raise ValueError(
                "Execution backtest input requires a market-scoped bar; "
                "resolve consolidated data through an Execution destination route first"
            )
        if bar.provider is None:
            raise ValueError("Execution backtest bar requires provider provenance")
        return ExecutionBacktestMarketRequest.bar(
                market_id=str(bar.scope.market_id),
                instrument_id=str(bar.instrument_id),
                timeframe=bar.bar_spec_id,
                open=format(_market_decimal(bar.open), "f"),
                high=format(_market_decimal(bar.high), "f"),
                low=format(_market_decimal(bar.low), "f"),
                close=format(_market_decimal(bar.close), "f"),
                volume=None
                if bar.volume is None
                else format(_market_decimal(bar.volume), "f"),
                observed_at_unix_nanos=bar.source_observed_at_unix_nanos,
                source_id=bar.provider,
                derivation="provider",
        )
    return None


def _market_decimal(value: DecimalValue) -> Decimal:
    return value.value


def map_execution_intent(value: object) -> ExecutionIntent:
    root = value
    intent = _field(root, "intent", root)
    status = _enum(
        IntentStatus,
        _field(root, "status", _field(intent, "status", "unknown")),
        IntentStatus.UNKNOWN,
    )
    instrument_id = _required(intent, "instrument_id")
    source_event_sequence = _field(intent, "source_event_sequence")
    return ExecutionIntent(
        id=IntentId(_required(intent, "intent_id")),
        strategy_id=_required(root, "strategy_id"),
        instrument=_instrument(instrument_id),
        account_ids=tuple(
            AccountId(str(item))
            for item in _sequence(_field(intent, "account_ids", ()), "account_ids")
        ),
        target_quantity=_quantity(_field(intent, "target_quantity")),
        status=status,
        reason=str(_field(intent, "reason", "")),
        order_ids=tuple(
            OrderId(str(item))
            for item in _sequence(_field(root, "order_ids", ()), "order_ids")
        ),
        source_event_sequence=(
            source_event_sequence
            if isinstance(source_event_sequence, int) and source_event_sequence > 0
            else None
        ),
        strategy_decision_id=(
            str(_field(intent, "strategy_decision_id"))
            if isinstance(_field(intent, "strategy_decision_id"), str)
            and str(_field(intent, "strategy_decision_id")).strip()
            else None
        ),
    )


def map_execution_order(value: object) -> Order:
    row = value
    instrument_id = _required(row, "instrument_id")
    updated_nanos = _field(row, "updated_at_unix_nanos")
    updated_at = (
        datetime_from_unix_nanos(updated_nanos)
        if isinstance(updated_nanos, int)
        else None
    )
    raw_intent_id = _field(row, "intent_id")
    quantity = _required_quantity(_field(row, "quantity"), "quantity")
    filled_quantity = _required_quantity(
        _field(row, "filled_quantity"), "filled_quantity"
    )
    return Order(
        id=OrderId(_required(row, "order_id")),
        strategy_id=_required(row, "strategy_id"),
        intent_id=IntentId(raw_intent_id)
        if isinstance(raw_intent_id, str) and raw_intent_id.strip()
        else None,
        instrument=_instrument(instrument_id),
        account_id=AccountId(_required(row, "account_id")),
        side=_enum(OrderSide, _field(row, "side"), OrderSide.BUY),
        quantity=quantity,
        filled_quantity=filled_quantity,
        limit_price=_price(_field(row, "limit_price")),
        status=_enum(OrderStatus, _field(row, "status"), OrderStatus.UNKNOWN),
        updated_at=updated_at,
    )


def map_execution_fill(value: object) -> Fill:
    row = value
    nanos = _field(row, "occurred_at_unix_nanos")
    if not isinstance(nanos, int):
        raise ValueError("Execution fill occurred_at_unix_nanos must be an integer")
    instrument_id = _required(row, "instrument_id")
    quantity = _required_quantity(_field(row, "quantity"), "quantity")
    price = _required_price(_field(row, "price"), "price")
    return Fill(
        id=FillId(_required(row, "fill_id")),
        order_id=OrderId(_required(row, "order_id")),
        instrument=_instrument(instrument_id),
        quantity=quantity,
        price=price,
        occurred_at=datetime_from_unix_nanos(nanos),
        intent_id=(
            IntentId(str(_field(row, "intent_id")))
            if isinstance(_field(row, "intent_id"), str)
            and str(_field(row, "intent_id")).strip()
            else None
        ),
    )


def map_execution_backtest_result(value: object) -> ExecutionBacktestResult:
    fills = getattr(value, "fills")
    if isinstance(fills, (str, bytes)) or not isinstance(fills, Sequence):
        raise ValueError("Execution backtest fills must be an array")
    return ExecutionBacktestResult(tuple(map_execution_fill(fill) for fill in fills))


def map_order_commitment(value: object) -> OrderCommitment:
    row = value
    remaining_quantity = _required_quantity(
        _field(row, "remaining_quantity"), "remaining_quantity"
    )
    return OrderCommitment(
        order_id=OrderId(_required(row, "order_id")),
        account_id=AccountId(_required(row, "account_id")),
        segment_key=SegmentKey(_required(row, "segment_key")),
        instrument_id=InstrumentId(_required(row, "instrument_id")),
        resource_kind=_required(row, "resource_kind"),
        resource_id=_required(row, "resource_id"),
        amount=_exact_decimal(_field(row, "amount")),
        remaining_quantity=remaining_quantity,
        status=CommitmentStatus(str(_field(row, "status", "uncertain"))),
        basis_kind=_required(row, "basis_kind"),
        updated_at_unix_nanos=_required_int(
            _field(row, "updated_at_unix_nanos"), "updated_at_unix_nanos"
        ),
    )


def map_risk_reservation(value: object) -> RiskReservationSaga:
    row = value
    funding_row = _field(row, "funding_requirement")
    amount = _required_money(_field(row, "amount"), "amount")
    return RiskReservationSaga(
        order_id=OrderId(_required(row, "order_id")),
        reservation_id=_required(row, "reservation_id"),
        idempotency_key=_required(row, "idempotency_key"),
        account_id=AccountId(_required(row, "account_id")),
        amount=amount,
        status=RiskReservationSagaStatus(str(_field(row, "status", "uncertain"))),
        risk_generation=_required_int(
            _field(row, "risk_generation"), "risk_generation"
        ),
        risk_event_sequence=_required_int(
            _field(row, "risk_event_sequence"), "risk_event_sequence"
        ),
        policy_version=_required_int(_field(row, "policy_version"), "policy_version"),
        expires_at_unix_nanos=_required_int(
            _field(row, "expires_at_unix_nanos"), "expires_at_unix_nanos"
        ),
        updated_at_unix_nanos=_required_int(
            _field(row, "updated_at_unix_nanos"), "updated_at_unix_nanos"
        ),
        funding_requirement=(
            None
            if funding_row is None
            else ExecutionFundingRequirement(
                required_margin=_required_money(
                    _field(funding_row, "required_margin"), "required_margin"
                ),
                available_margin=_required_money(
                    _field(funding_row, "available_margin"), "available_margin"
                ),
                shortfall=_required_money(
                    _field(funding_row, "shortfall"), "shortfall"
                ),
                margin_rule_id=_required(funding_row, "margin_rule_id"),
                risk_decision_id=_required(funding_row, "risk_decision_id"),
                risk_policy_version=_required_int(
                    _field(funding_row, "risk_policy_version"),
                    "risk_policy_version",
                ),
                account_snapshot_watermark=_required_int(
                    _field(funding_row, "account_snapshot_watermark"),
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


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _field(value: object, name: str, default: object = None) -> object:
    return getattr(value, name, default)


def _required(value: object, name: str) -> str:
    raw = _field(value, name)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Execution value is missing {name}")
    return raw


def _quantity(value: object) -> QuantityLike | None:
    return _semantic_decimal(value, QuantityLike, Quantity)


def _price(value: object) -> PriceLike | None:
    return _semantic_decimal(value, PriceLike, Price)


def _money(value: object) -> MoneyLike | None:
    return _semantic_decimal(value, MoneyLike, Money)


def _required_quantity(value: object, name: str) -> QuantityLike:
    result = _quantity(value)
    if result is None:
        raise ValueError(f"Execution value is missing {name}")
    return result


def _required_price(value: object, name: str) -> PriceLike:
    result = _price(value)
    if result is None:
        raise ValueError(f"Execution value is missing {name}")
    return result


def _required_money(value: object, name: str) -> MoneyLike:
    result = _money(value)
    if result is None:
        raise ValueError(f"Execution value is missing {name}")
    return result


def _exact_decimal(value: object) -> DecimalValue:
    if isinstance(value, DecimalValue):
        return value
    raise ValueError("commitment amount must satisfy DecimalValue")


def _semantic_decimal(value: object, protocol, concrete):
    if value is None:
        return None
    if isinstance(value, protocol):
        return value
    if isinstance(value, (Decimal, str, int)) and not isinstance(value, bool):
        return concrete(value)
    raise ValueError(
        f"Execution contract decimal value must satisfy {protocol.__name__}"
    )


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
