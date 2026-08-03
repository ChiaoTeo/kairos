from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from kairospy.application.usecases.account.domain.private_stream import PrivateStreamCheckpoint
from kairospy.domain.account import AccountContext, AccountEvent, AccountEventKind, AccountSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.infrastructure.integrations.application.account import ConnectionAccountStreamRequest
from kairospy.domain.order import OrderState


@dataclass(slots=True)
class LivePrivateStreamState:
    _seen_order_updates: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    _seen_trade_updates: set[tuple[str, str, str, str, str]] = field(default_factory=set)
    _order_timestamps: dict[str, Decimal] = field(default_factory=dict)

    def checkpoint(self) -> PrivateStreamCheckpoint:
        return PrivateStreamCheckpoint(
            seen_order_updates=tuple(sorted(self._seen_order_updates)),
            seen_trade_updates=tuple(sorted(self._seen_trade_updates)),
            order_timestamps={key: str(value) for key, value in sorted(self._order_timestamps.items())},
        )

    def restore_checkpoint(self, checkpoint: PrivateStreamCheckpoint) -> None:
        self._seen_order_updates = set(checkpoint.seen_order_updates)
        self._seen_trade_updates = set(checkpoint.seen_trade_updates)
        self._order_timestamps = {str(key): Decimal(str(value)) for key, value in checkpoint.order_timestamps.items()}

    def snapshot(self) -> dict[str, object]:
        return self.checkpoint().to_dict()

    @classmethod
    def from_snapshot(cls, value: Mapping[str, object]) -> "LivePrivateStreamState":
        state = cls()
        state.restore_checkpoint(PrivateStreamCheckpoint.from_dict(value))
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
        identity = str(raw.get("id") or raw.get("clientOrderId") or raw.get("order_id") or "").strip()
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

    def accept_typed_update(self, update: ExecutionUpdate, *, trades_only: bool = False) -> bool:
        key = (
            str(update.order_venue_id),
            str(update.kind),
            str(update.fill_quantity),
            str(update.fill_price),
            str(update.observed_at),
        )
        seen = self._seen_trade_updates if trades_only else self._seen_order_updates
        if key in seen:
            return False
        seen.add(key)
        return True


@dataclass(slots=True)
class LivePrivateStreamCollector:
    gateway: object
    account: AccountContext
    coordinator: object
    payload_adapter: object
    account_event: Any
    incident_event: Any
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
    ) -> tuple[object, ...]:
        if callable(getattr(self.gateway, "account_snapshots", None)) and callable(getattr(self.gateway, "execution_updates", None)):
            return await self._collect_typed(
                snapshot,
                symbol=symbol,
                max_balance_events=max_balance_events,
                max_order_events=max_order_events,
                max_trade_events=max_trade_events,
            )
        events: list[object] = []
        if max_balance_events:
            async for raw in _take(self.gateway.watch_balance(params=balance_params), max_balance_events):
                at = datetime.now(timezone.utc)
                try:
                    previous = snapshot
                    snapshot = self.payload_adapter.balance_snapshot(self.account, raw, at=at, open_orders=snapshot.open_orders)
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

    async def _collect_typed(
        self,
        snapshot: AccountSnapshot,
        *,
        symbol: str | None,
        max_balance_events: int,
        max_order_events: int,
        max_trade_events: int,
    ) -> tuple[object, ...]:
        """Consume the typed Integration stream without a vendor parser."""

        events: list[object] = []
        request = ConnectionAccountStreamRequest(
            context=self.account,
            symbol=symbol,
            open_orders=snapshot.open_orders,
        )
        if max_balance_events:
            async for current in _take_typed(self.gateway.account_snapshots(request), max_balance_events):
                at = current.observed_at or datetime.now(timezone.utc)
                previous = snapshot
                snapshot = current
                self._record_balance_adjustments(previous, snapshot, raw={}, at=at)
                events.append(self.account_event(at, snapshot))
        if max_order_events:
            async for update in _take_typed(self.gateway.execution_updates(request), max_order_events):
                if not self.state.accept_typed_update(update):
                    continue
                state = self.coordinator.apply_execution_update(update)
                at = state.updated_at or update.observed_at or datetime.now(timezone.utc)
                events.append(self.account_event(at, snapshot))
        if max_trade_events:
            async for update in _take_typed(self.gateway.execution_updates(request, trades_only=True), max_trade_events):
                if not self.state.accept_typed_update(update, trades_only=True):
                    continue
                state = self.coordinator.apply_execution_update(update)
                at = state.updated_at or update.observed_at or datetime.now(timezone.utc)
                events.append(self.account_event(at, snapshot))
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
                    self.account.book,
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


async def _take_typed(events: AsyncIterator[object], limit: int) -> AsyncIterator[object]:
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
    "LivePrivateStreamCollector",
    "LivePrivateStreamState",
    "classify_balance_delta",
]
