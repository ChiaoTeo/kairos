from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from kairospy.core.order import OrderState
from kairospy.core.views import ViewFieldSchema, ViewSchema

from kairospy.core.account.model import (
    AccountBalance,
    AccountContext,
    AccountSource,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.core.account.state import AccountState


class AccountPayload(Protocol):
    context: AccountContext
    snapshot: object | None
    account_state: AccountState | None
    equity: Decimal | None
    unrealized_pnl: Decimal | None
    source: AccountSource | str | None
    pending_orders: tuple[OrderState, ...]


class AccountEventEnvelope(Protocol):
    domain: str
    time: datetime
    payload: object
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AccountCurrentView:
    context: AccountContext | None = None
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
    cash: Decimal | None = None
    equity: Decimal | None = None
    initial_equity: Decimal | None = None
    net_profit: Decimal | None = None
    total_return: Decimal | None = None
    metadata: Mapping[str, object] | None = None


class AccountCurrentProjection:
    """Projection for account state across backtest, simulation, paper, and live runs."""

    def __init__(
        self,
        context: AccountContext,
        *,
        key: str | None = None,
        equity_currency: str | None = None,
        initial_equity: Decimal | str | int | float | None = None,
    ) -> None:
        self.context = context
        self.key = key or _account_view_key(context)
        self.equity_currency = equity_currency
        self._event_count = 0
        self._last_event: AccountEventEnvelope | None = None
        self._initial_equity = None if initial_equity is None else Decimal(str(initial_equity))
        self._last_equity: Decimal | None = None
        self.schema = ViewSchema(
            self.key,
            "system",
            fields=(
                ViewFieldSchema("context", "account identity and environment", "runtime account event", "account event"),
                ViewFieldSchema("event_count", "consumed account event count", "runtime sequence", "account event"),
                ViewFieldSchema("last_event_time", "latest account event time", "event time", "account event"),
                ViewFieldSchema("source", "account data source", "event time", "account event"),
                ViewFieldSchema("balances", "account balances", "event time", "account snapshot or state"),
                ViewFieldSchema("margins", "account margin states", "event time", "account state"),
                ViewFieldSchema("positions", "account positions", "event time", "account snapshot or state"),
                ViewFieldSchema("open_orders", "venue open orders", "event time", "account state"),
                ViewFieldSchema("pending_orders", "local active order states", "runtime state", "order journal"),
                ViewFieldSchema("stale", "account state staleness flag", "event time", "account state"),
                ViewFieldSchema("account_state", "complete account state", "event time", "account state"),
                ViewFieldSchema("cash", "cash in selected equity currency", "event time", "account snapshot or state"),
                ViewFieldSchema("equity", "marked account equity", "event time", "account event"),
                ViewFieldSchema("initial_equity", "first or configured account equity baseline", "run baseline", "runtime"),
                ViewFieldSchema("net_profit", "equity minus baseline", "event time", "runtime account view"),
                ViewFieldSchema("total_return", "net profit divided by baseline", "event time", "runtime account view"),
                ViewFieldSchema("metadata", "non-domain account event metadata", "event time", "account event"),
            ),
            mutability="runtime_writable",
            evidence="runtime account event projection",
        )

    def on_event(self, event: AccountEventEnvelope) -> None:
        payload = _account_payload(event)
        if event.domain != "account" or payload is None or payload.context != self.context:
            return
        self._event_count += 1
        self._last_event = event
        equity = self._equity(payload)
        if equity is not None:
            if self._initial_equity is None:
                self._initial_equity = equity
            self._last_equity = equity

    def view(self) -> AccountCurrentView:
        if self._last_event is None:
            return AccountCurrentView(context=self.context, initial_equity=self._initial_equity)

        event = self._last_event
        payload = _account_payload(event)
        if payload is None:
            raise RuntimeError("account projection received non-account payload")
        balances = self._balances(payload)
        account_state = payload.account_state
        cash = self._cash(balances)
        equity = self._last_equity
        initial = self._initial_equity
        net_profit = None if equity is None or initial is None else equity - initial
        total_return = None if net_profit is None or initial in (None, Decimal("0")) else net_profit / initial
        return AccountCurrentView(
            context=self.context,
            event_count=self._event_count,
            last_event_time=event.time,
            source=payload.source,
            balances=balances,
            margins=self._margins(payload),
            positions=self._positions(payload),
            open_orders=() if account_state is None else account_state.open_orders,
            pending_orders=payload.pending_orders,
            stale=False if account_state is None else account_state.stale,
            account_state=account_state,
            cash=cash,
            equity=equity,
            initial_equity=initial,
            net_profit=net_profit,
            total_return=total_return,
            metadata=dict(event.metadata or {}),
        )

    def _balances(self, event: AccountPayload) -> tuple[AccountBalance, ...]:
        if event.account_state is not None:
            return event.account_state.balances
        if event.snapshot is not None and hasattr(event.snapshot, "balances"):
            return event.snapshot.balances
        return ()

    def _margins(self, event: AccountPayload) -> tuple[MarginState, ...]:
        if event.account_state is not None:
            return event.account_state.margins
        if event.snapshot is not None and hasattr(event.snapshot, "margins"):
            return event.snapshot.margins
        return ()

    def _positions(self, event: AccountPayload) -> tuple[PositionSnapshot, ...]:
        if event.account_state is not None:
            return event.account_state.positions
        if event.snapshot is not None and hasattr(event.snapshot, "positions"):
            return event.snapshot.positions
        return ()

    def _cash(self, balances: tuple[AccountBalance, ...]) -> Decimal | None:
        currency = self.equity_currency
        if currency is None and balances:
            currency = balances[0].currency
        if currency is None:
            return None
        balance = next((item for item in balances if item.currency == currency), None)
        return None if balance is None else balance.total

    def _equity(self, event: AccountPayload) -> Decimal | None:
        if event.equity is not None:
            return event.equity
        balances = self._balances(event)
        cash = self._cash(balances)
        if cash is None:
            return None
        if event.unrealized_pnl is not None:
            return cash + event.unrealized_pnl
        return cash


def _account_payload(event: AccountEventEnvelope) -> AccountPayload | None:
    payload = event.payload
    return payload if hasattr(payload, "context") and hasattr(payload, "account_state") else None


def _account_view_key(context: AccountContext) -> str:
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


def _key_part(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value.lower()).strip("_")


__all__ = ["AccountCurrentProjection", "AccountCurrentView"]
