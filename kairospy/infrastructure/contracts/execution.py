"""Python-facing Execution contract."""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal
import sys
from typing import Any, cast

from kairospy.application.execution import (
    ExecutionIntent,
    IntentStatus,
    Order,
    OrderSide,
    OrderStatus,
)
from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import (
    AccountId,
    InstrumentId,
    IntentId,
    OrderId,
    datetime_from_unix_nanos,
)
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.execution.v1.OrdersSnapshot import (
        OrdersSnapshot,
    )

    return MmapSnapshotReader(path, file_identifier=b"PEO1", root_type=OrdersSnapshot)


class ExecutionMmapProjection:
    """Synchronous Execution intent/order projection decoded from mmap."""

    def __init__(self, orders_path: str | Path, intents_path: str | Path) -> None:
        sys.modules.setdefault("kairos", _generated_kairos)
        from kairospy.infrastructure.transport.generated.kairos.intent.v1.IntentSnapshot import (
            IntentSnapshot,
        )

        self._orders = snapshot_reader(orders_path)
        self._intents = MmapSnapshotReader(
            intents_path, file_identifier=b"PIJ1", root_type=IntentSnapshot
        )

    def get_order(self, order_id: str) -> Order | None:
        return next(
            (value for value in self.orders() if str(value.id) == order_id), None
        )

    def open_orders(self, *, account_id: str | None = None) -> tuple[Order, ...]:
        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }
        return tuple(
            value
            for value in self.orders()
            if value.status not in terminal
            and (account_id is None or str(value.account_id) == account_id)
        )

    def orders(self) -> tuple[Order, ...]:
        contract = self._orders.read()
        from kairospy.infrastructure.transport.generated.kairos.execution.v1.OrdersSnapshot import (
            OrdersSnapshot,
        )

        payload = cast(Any, OrdersSnapshot.GetRootAs(contract.payload, 0).Payload())
        if payload is None:
            raise ValueError("Execution orders snapshot payload is missing")
        return tuple(
            _order(value, contract.metadata.event_sequence)
            for value in _table_items(payload, "Orders", "Execution")
        )

    def get_intent(self, intent_id: str) -> ExecutionIntent | None:
        contract = self._intents.read()
        from kairospy.infrastructure.transport.generated.kairos.intent.v1.IntentSnapshot import (
            IntentSnapshot,
        )

        payload = cast(Any, IntentSnapshot.GetRootAs(contract.payload, 0).Payload())
        if payload is None:
            raise ValueError("Execution intent snapshot payload is missing")
        raw = next(
            (
                value
                for value in _table_items(payload, "Intents", "Execution")
                if _text(value.IntentId()) == intent_id
            ),
            None,
        )
        return None if raw is None else _intent(raw, contract.metadata.event_sequence)


def _order(value: Any, event_sequence: int) -> Order:
    instrument_id = _required_text(value.InstrumentId(), "order instrument_id")
    status = (_text(value.Status()) or "unknown").lower()
    return Order(
        id=OrderId(_required_text(value.OrderId(), "order_id")),
        intent_id=_optional_id(value.IntentId(), IntentId),
        instrument=_instrument(instrument_id),
        account_id=AccountId(_required_text(value.AccountId(), "order account_id")),
        side=OrderSide.BUY if int(value.Side()) == 1 else OrderSide.SELL,
        quantity=_required_decimal(value.Quantity(), "order quantity"),
        filled_quantity=_decimal64(value.FilledQuantity()) or Decimal("0"),
        limit_price=_decimal64(value.LimitPrice()),
        status=OrderStatus(status)
        if status in OrderStatus._value2member_map_
        else OrderStatus.UNKNOWN,
        updated_at=None
        if int(value.UpdatedAtUnixNanos()) == 0
        else datetime_from_unix_nanos(int(value.UpdatedAtUnixNanos())),
        event_sequence=event_sequence,
    )


def _intent(value: Any, event_sequence: int) -> ExecutionIntent:
    instrument_id = _required_text(value.InstrumentId(), "intent instrument_id")
    status = (_text(value.Status()) or "unknown").lower()
    return ExecutionIntent(
        id=IntentId(_required_text(value.IntentId(), "intent_id")),
        instrument=_instrument(instrument_id),
        account_ids=tuple(
            AccountId(_required_text(value.AccountIds(index), "intent account_id"))
            for index in range(value.AccountIdsLength())
        ),
        target_quantity=_decimal64(value.TargetQuantity()),
        status=IntentStatus(status)
        if status in IntentStatus._value2member_map_
        else IntentStatus.UNKNOWN,
        reason=_text(value.Reason()) or "",
        order_ids=tuple(
            OrderId(_required_text(value.OrderIds(index), "intent order_id"))
            for index in range(value.OrderIdsLength())
        ),
        event_sequence=event_sequence,
    )


def _table_items(value: object, name: str, owner: str) -> tuple[Any, ...]:
    table = cast(Any, value)
    result = tuple(
        getattr(table, name)(index)
        for index in range(int(getattr(table, f"{name}Length")()))
    )
    if any(item is None for item in result):
        raise ValueError(f"{owner} snapshot contains an empty {name} entry")
    return cast(tuple[Any, ...], result)


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _required_text(value: bytes | None, name: str) -> str:
    result = _text(value)
    if result is None or not result.strip():
        raise ValueError(f"{name} is required")
    return result


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = cast(Any, value)
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


def _required_decimal(value: object | None, name: str) -> Decimal:
    result = _decimal64(value)
    if result is None:
        raise ValueError(f"{name} is required")
    return result


def _instrument(value: str) -> InstrumentRef:
    identifier = InstrumentId(value)
    return InstrumentRef(identifier, value.rsplit(":", 1)[-1])


def _optional_id(value: bytes | None, kind):
    raw = _text(value)
    return None if raw is None or not raw.strip() else kind(raw)


def advance_time(path: str | Path, event_time_unix_nanos: int) -> dict:
    """Advance Execution's replay business clock at an explicit barrier."""
    status, value = UnixJsonCommandClient(path).request(
        "POST",
        "/v1/time/advance",
        {"event_time_unix_nanos": int(event_time_unix_nanos)},
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"execution time advance failed with status {status}")
        )
    return value


def backtest_run(path: str | Path, request: dict) -> dict:
    """Run deterministic simulated execution against supplied market events."""
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/run", request
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"backtest run failed with status {status}")
        )
    return value


def backtest_market(path: str | Path, event) -> dict:
    """Forward one strategy-visible market event to simulated Execution.

    Bars remain Bars at the contract boundary.  Execution owns the explicit
    deterministic bar-to-quote policy, so the Strategy adapter does not
    manufacture a fake Quote and lose the observation type.
    """
    from kairospy.infrastructure.transport.market import BarView, QuoteView

    if event.kind == "quote" and isinstance(event.payload, QuoteView):
        quote = event.payload
        body = {
            "Quote": {
                "market_id": quote.market_id or "",
                "instrument_id": quote.instrument_id,
                "bid_price": None if quote.bid_price is None else quote.bid_price.value,
                "bid_quantity": None
                if quote.bid_quantity is None
                else quote.bid_quantity.value,
                "ask_price": None if quote.ask_price is None else quote.ask_price.value,
                "ask_quantity": None
                if quote.ask_quantity is None
                else quote.ask_quantity.value,
                "observed_at_unix_nanos": quote.event_time_unix_nanos,
                "source_id": quote.source_id or "strategy-market",
            }
        }
    elif event.kind == "bar" and isinstance(event.payload, BarView):
        bar = event.payload
        body = {
            "Bar": {
                "market_id": bar.market_id or "",
                "instrument_id": bar.instrument_id,
                "timeframe": bar.timeframe,
                "open": bar.open.value,
                "high": bar.high.value,
                "low": bar.low.value,
                "close": bar.close.value,
                "volume": None if bar.volume is None else bar.volume.value,
                "observed_at_unix_nanos": bar.event_time_unix_nanos,
                "source_id": bar.source_id or "strategy-market",
                "derivation": bar.derivation or "provider",
            }
        }
    else:
        return {"fills": []}
    status, value = UnixJsonCommandClient(path).request(
        "POST", "/v1/backtest/market", body
    )
    if status >= 400:
        raise RuntimeError(
            value.get("error", f"market simulation failed with status {status}")
        )
    return value


__all__ = [
    "ExecutionMmapProjection",
    "CommandEnvelope",
    "QueryEnvelope",
    "backtest_market",
    "advance_time",
    "backtest_run",
    "snapshot_reader",
]
