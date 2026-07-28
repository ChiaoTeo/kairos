from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import AsyncIterator, Callable, Mapping, Protocol
from uuid import uuid4

from kairospy.core.account import AccountContext, AccountEvent, AccountEventKind, AccountSnapshot
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.order import OrderState
from kairospy.application.runtime.model import RuntimeDataEnvelope


AccountEventFactory = Callable[[datetime, AccountSnapshot], RuntimeDataEnvelope]
IncidentEventFactory = Callable[[str, Exception, Mapping[str, object], datetime | None], RuntimeDataEnvelope]


class LiveAccountStreamGateway(Protocol):
    def watch_balance(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...

    def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        ...


class LivePrivateStreamPayloadAdapter(Protocol):
    def balance_snapshot(
        self,
        context: AccountContext,
        raw_balance: Mapping[str, object],
        *,
        at: datetime,
        open_orders: tuple,
    ) -> AccountSnapshot:
        ...

    def ingest_order_update(
        self,
        coordinator: ExecutionCoordinator,
        context: AccountContext,
        raw: Mapping[str, object],
    ) -> OrderState:
        ...

    def ingest_trade_update(
        self,
        coordinator: ExecutionCoordinator,
        context: AccountContext,
        raw: Mapping[str, object],
    ) -> OrderState:
        ...


@dataclass(slots=True)
class LivePrivateStreamState:
    _seen_order_updates: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    _seen_trade_updates: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    _order_timestamps: dict[str, Decimal] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return {
            "seen_order_updates": [list(item) for item in sorted(self._seen_order_updates)],
            "seen_trade_updates": [list(item) for item in sorted(self._seen_trade_updates)],
            "order_timestamps": {key: str(value) for key, value in sorted(self._order_timestamps.items())},
        }

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "LivePrivateStreamState":
        state = cls()
        state._seen_order_updates = set(_tuple_key(item, 5) for item in _list(value.get("seen_order_updates")))
        state._seen_trade_updates = set(_tuple_key(item, 5) for item in _list(value.get("seen_trade_updates")))
        timestamps = value.get("order_timestamps")
        if isinstance(timestamps, Mapping):
            state._order_timestamps = {
                str(key): Decimal(str(item))
                for key, item in timestamps.items()
            }
        return state

    def accept_order(self, raw: Mapping[str, object]) -> bool:
        key = (
            str(raw.get("id") or ""),
            str(raw.get("status") or ""),
            str(raw.get("filled") or ""),
            str(raw.get("remaining") or ""),
            str(raw.get("timestamp") or raw.get("lastTradeTimestamp") or ""),
        )
        if key in self._seen_order_updates:
            return False
        identity = str(raw.get("id") or raw.get("clientOrderId") or raw.get("client_order_id") or "").strip()
        timestamp = _event_timestamp(raw)
        if identity and timestamp is not None:
            latest = self._order_timestamps.get(identity)
            if latest is not None and timestamp < latest:
                return False
            self._order_timestamps[identity] = timestamp
        self._seen_order_updates.add(key)
        return True

    def accept_trade(self, raw: Mapping[str, object]) -> bool:
        key = (
            str(raw.get("id") or ""),
            str(raw.get("order") or raw.get("orderId") or ""),
            str(raw.get("amount") or ""),
            str(raw.get("price") or ""),
            str(raw.get("timestamp") or ""),
        )
        if key in self._seen_trade_updates:
            return False
        self._seen_trade_updates.add(key)
        return True


@dataclass(slots=True)
class LivePrivateStreamCollector:
    gateway: LiveAccountStreamGateway
    account: AccountContext
    coordinator: ExecutionCoordinator
    payload_adapter: LivePrivateStreamPayloadAdapter
    account_event: AccountEventFactory
    incident_event: IncidentEventFactory
    state: LivePrivateStreamState = field(default_factory=LivePrivateStreamState)

    async def collect(
        self,
        snapshot: AccountSnapshot,
        *,
        symbol: str | None,
        balance_params: Mapping[str, object] | None,
        order_params: Mapping[str, object] | None,
        max_balance_events: int,
        max_order_events: int,
        max_trade_events: int,
    ) -> tuple[RuntimeDataEnvelope, ...]:
        events: list[RuntimeDataEnvelope] = []
        if max_balance_events:
            async for raw in _take(self.gateway.watch_balance(params=balance_params), max_balance_events):
                at = datetime.now(timezone.utc)
                try:
                    previous = snapshot
                    snapshot = self.payload_adapter.balance_snapshot(
                        self.account,
                        raw,
                        at=at,
                        open_orders=snapshot.open_orders,
                    )
                    self._record_balance_adjustments(previous, snapshot, raw=raw, at=at)
                    events.append(self.account_event(at, snapshot))
                except Exception as error:
                    events.append(self.incident_event("live.account.balance.error", error, raw, at))
        if max_order_events:
            async for raw in _take(self.gateway.watch_orders(symbol, params=order_params), max_order_events):
                if not self.state.accept_order(raw):
                    continue
                try:
                    state = self.payload_adapter.ingest_order_update(self.coordinator, self.account, raw)
                    at = state.updated_at or datetime.now(timezone.utc)
                    events.append(self.account_event(at, snapshot))
                except Exception as error:
                    events.append(self.incident_event("live.account.order.error", error, raw, None))
        if max_trade_events:
            async for raw in _take(self.gateway.watch_my_trades(symbol, params=order_params), max_trade_events):
                if not self.state.accept_trade(raw):
                    continue
                try:
                    state = self.payload_adapter.ingest_trade_update(self.coordinator, self.account, raw)
                    at = state.updated_at or datetime.now(timezone.utc)
                    events.append(self.account_event(at, snapshot))
                except Exception as error:
                    events.append(self.incident_event("live.account.trade.error", error, raw, None))
        return tuple(events)

    def _record_balance_adjustments(
        self,
        previous: AccountSnapshot,
        current: AccountSnapshot,
        *,
        raw: Mapping[str, object],
        at: datetime,
    ) -> None:
        previous_totals = {item.currency: item.total for item in previous.balances}
        current_totals = {item.currency: item.total for item in current.balances}
        for currency in sorted(set(previous_totals) | set(current_totals)):
            delta = current_totals.get(currency, Decimal("0")) - previous_totals.get(currency, Decimal("0"))
            if delta == 0:
                continue
            self.coordinator.ledger.record(
                AccountEvent(
                    uuid4(),
                    self.account.account,
                    classify_balance_delta(raw, delta),
                    at,
                    currency,
                    cash_delta=delta,
                    reference_id=f"balance:{currency}:{at.isoformat()}",
                )
            )


async def _take(events: AsyncIterator[Mapping[str, object]], limit: int) -> AsyncIterator[Mapping[str, object]]:
    count = 0
    async for event in events:
        yield event
        count += 1
        if count >= limit:
            break


def _event_timestamp(raw: Mapping[str, object]) -> Decimal | None:
    value = raw.get("timestamp") or raw.get("lastTradeTimestamp")
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _list(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, list) else ()


def _tuple_key(value: object, size: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError("private stream state key has invalid shape")
    return tuple(str(item) for item in value)


def classify_balance_delta(raw: Mapping[str, object], delta: Decimal) -> AccountEventKind:
    reason = _balance_reason(raw)
    if reason in {"deposit", "transfer_in"}:
        return AccountEventKind.DEPOSIT if delta > 0 else AccountEventKind.ADJUSTMENT
    if reason in {"withdraw", "withdrawal", "transfer_out"}:
        return AccountEventKind.WITHDRAWAL if delta < 0 else AccountEventKind.ADJUSTMENT
    if reason in {"funding", "funding_fee"}:
        return AccountEventKind.FUNDING
    if reason in {"settlement", "delivery", "expiry"}:
        return AccountEventKind.SETTLEMENT
    return AccountEventKind.ADJUSTMENT


def _balance_reason(raw: Mapping[str, object]) -> str:
    for key in ("type", "kind", "reason", "eventType", "event_type"):
        value = raw.get(key)
        if value is not None:
            return _normalize_reason(value)
    info = raw.get("info")
    if isinstance(info, Mapping):
        for key in ("type", "kind", "reason", "eventType", "event_type", "e", "m"):
            value = info.get(key)
            if value is not None:
                return _normalize_reason(value)
    return ""


def _normalize_reason(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


__all__ = [
    "LiveAccountStreamGateway",
    "LivePrivateStreamCollector",
    "LivePrivateStreamPayloadAdapter",
    "LivePrivateStreamState",
    "classify_balance_delta",
]
