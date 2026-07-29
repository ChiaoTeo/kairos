from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.application.runtime.ports import AccountPort
from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    AccountState,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.core.order import OrderState
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class AccountCurrentView:
    context: AccountContext
    event_count: int = 0
    last_event_time: datetime | None = None
    source: AccountSource | str | None = None
    balances: tuple[AccountBalance, ...] = ()
    margins: tuple[MarginState, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    open_orders: tuple[OpenOrderSnapshot, ...] = ()
    pending_orders: tuple[OrderState, ...] = ()
    stale: bool = False
    account_state: AccountState | None = None
    snapshot: AccountSnapshot | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    initial_equity: Decimal | None = None
    net_profit: Decimal | None = None
    total_return: Decimal | None = None
    metadata: dict[str, object] | None = None


class AccountCurrentViewState:
    def __init__(
        self,
        port: AccountPort,
        context: AccountContext,
        *,
        key: str | None = None,
        equity_currency: str | None = None,
        initial_equity: Decimal | str | int | float | None = None,
    ) -> None:
        self.port = port
        self.context = context
        self.key = key or account_current_view_key(context)
        self.equity_currency = equity_currency
        self._event_count = 0
        self._last_event: RuntimeEnvelope | None = None
        self._last_payload: object | None = None
        self._initial_equity = None if initial_equity is None else Decimal(str(initial_equity))
        self._last_equity: Decimal | None = None
        self.schema = ViewSchema(
            self.key,
            "system",
            fields=(
                ViewFieldSchema("context", "account identity and environment", "runtime account event", "account port"),
                ViewFieldSchema("event_count", "consumed account event count", "runtime sequence", "account view state"),
                ViewFieldSchema("last_event_time", "latest account event time", "event time", "account event"),
                ViewFieldSchema("source", "account data source", "event time", "account state or snapshot"),
                ViewFieldSchema("balances", "account balances", "event time", "account state or snapshot"),
                ViewFieldSchema("margins", "account margin states", "event time", "account state or snapshot"),
                ViewFieldSchema("positions", "account positions", "event time", "account state or snapshot"),
                ViewFieldSchema("open_orders", "venue open orders", "event time", "account state or snapshot"),
                ViewFieldSchema("pending_orders", "local active order states", "runtime state", "account event"),
                ViewFieldSchema("stale", "account state staleness flag", "event time", "account state"),
                ViewFieldSchema("account_state", "complete account state", "event time", "account port"),
                ViewFieldSchema("snapshot", "latest account snapshot", "event time", "account port"),
                ViewFieldSchema("cash", "cash in selected equity currency", "event time", "account balances"),
                ViewFieldSchema("equity", "marked account equity", "event time", "account event or balances"),
                ViewFieldSchema("initial_equity", "first or configured account equity baseline", "run baseline", "account view state"),
                ViewFieldSchema("net_profit", "equity minus baseline", "event time", "account view state"),
                ViewFieldSchema("total_return", "net profit divided by baseline", "event time", "account view state"),
                ViewFieldSchema("metadata", "non-domain account event metadata", "event time", "account event"),
            ),
            mutability="runtime_writable",
            evidence="runtime account view state",
        )

    def on_event(self, event: RuntimeEnvelope) -> None:
        if event.domain != "account":
            return
        payload = _account_payload(event.payload)
        if payload is None or _payload_context(payload) != self.context:
            return
        self._apply_payload(payload)
        self._event_count += 1
        self._last_event = event
        self._last_payload = payload
        equity = _payload_equity(payload)
        if equity is not None:
            if self._initial_equity is None:
                self._initial_equity = equity
            self._last_equity = equity

    def _apply_payload(self, payload: object) -> None:
        if not isinstance(payload, AccountSnapshot):
            return
        update_snapshot = getattr(self.port, "update_snapshot", None)
        if update_snapshot is not None:
            update_snapshot(payload)

    def view(self) -> AccountCurrentView:
        state = self.port.state(self.context.account)
        snapshot = self.port.snapshot(self.context.account)
        payload = self._last_payload
        payload_state = _payload_account_state(payload)
        payload_snapshot = _payload_snapshot(payload)
        state = payload_state or (None if payload_snapshot is not None else state)
        snapshot = payload_snapshot or snapshot
        balances = _balances(state, snapshot)
        margins = _margins(state, snapshot)
        positions = _positions(state, snapshot)
        open_orders = _open_orders(state, snapshot)
        cash = _cash(balances, self.equity_currency)
        equity = self._last_equity or _payload_equity(payload) or cash
        if equity is not None and self._initial_equity is None:
            self._initial_equity = equity
        net_profit = None if equity is None or self._initial_equity is None else equity - self._initial_equity
        total_return = None if net_profit is None or self._initial_equity in (None, Decimal("0")) else net_profit / self._initial_equity
        return AccountCurrentView(
            context=self.context,
            event_count=self._event_count,
            last_event_time=None if self._last_event is None else self._last_event.time,
            source=_source(state, snapshot, payload),
            balances=balances,
            margins=margins,
            positions=positions,
            open_orders=open_orders,
            pending_orders=_payload_pending_orders(payload),
            stale=False if state is None else state.stale,
            account_state=state,
            snapshot=snapshot,
            cash=cash,
            equity=equity,
            initial_equity=self._initial_equity,
            net_profit=net_profit,
            total_return=total_return,
            metadata=None if self._last_event is None else dict(getattr(self._last_event, "metadata", {}) or {}),
        )


def account_current_view_key(context: AccountContext) -> str:
    parts = [
        "account",
        "current",
        context.environment.value,
        context.account.broker,
        context.account.account_id,
    ]
    if context.account.segment:
        parts.append(context.account.segment)
    return ".".join(_key_part(part) for part in parts)


def _account_payload(payload: object) -> object | None:
    if isinstance(payload, (AccountState, AccountSnapshot)):
        return payload
    return payload if hasattr(payload, "context") else None


def _payload_context(payload: object) -> AccountContext | None:
    return getattr(payload, "context", None)


def _payload_account_state(payload: object | None) -> AccountState | None:
    if isinstance(payload, AccountState):
        return payload
    state = getattr(payload, "account_state", None)
    return state if isinstance(state, AccountState) else None


def _payload_snapshot(payload: object | None) -> AccountSnapshot | None:
    if isinstance(payload, AccountSnapshot):
        return payload
    snapshot = getattr(payload, "snapshot", None)
    return snapshot if isinstance(snapshot, AccountSnapshot) else None


def _payload_equity(payload: object | None) -> Decimal | None:
    value = getattr(payload, "equity", None)
    return None if value is None else Decimal(str(value))


def _payload_pending_orders(payload: object | None) -> tuple[OrderState, ...]:
    return tuple(getattr(payload, "pending_orders", ()) or ())


def _balances(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[AccountBalance, ...]:
    if state is not None:
        return state.balances
    return () if snapshot is None else snapshot.balances


def _margins(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[MarginState, ...]:
    if state is not None:
        return state.margins
    return () if snapshot is None else snapshot.margins


def _positions(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[PositionSnapshot, ...]:
    if state is not None:
        return state.positions
    return () if snapshot is None else snapshot.positions


def _open_orders(state: AccountState | None, snapshot: AccountSnapshot | None) -> tuple[OpenOrderSnapshot, ...]:
    if state is not None:
        return state.open_orders
    return () if snapshot is None else snapshot.open_orders


def _cash(balances: tuple[AccountBalance, ...], equity_currency: str | None) -> Decimal | None:
    currency = equity_currency
    if currency is None and balances:
        currency = balances[0].currency
    if currency is None:
        return None
    balance = next((item for item in balances if item.currency == currency), None)
    return None if balance is None else balance.total


def _source(
    state: AccountState | None,
    snapshot: AccountSnapshot | None,
    payload: object | None,
) -> AccountSource | str | None:
    value = getattr(payload, "source", None)
    if value is not None:
        return value
    if state is not None:
        return state.source
    if snapshot is not None:
        return snapshot.source
    return None


def _key_part(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value.lower()).strip("_")


__all__ = ["AccountCurrentViewState", "AccountCurrentView", "account_current_view_key"]
