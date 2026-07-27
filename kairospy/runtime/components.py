from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Protocol

from kairospy.accounts import (
    AccountBalance,
    AccountContext,
    AccountProjection,
    AccountSource,
    MarginState,
    OpenOrderSnapshot,
    PositionSnapshot,
)
from kairospy.orders import OrderState
from kairospy.strategy.views import ViewFieldSchema, ViewSchema

from .events import AccountRuntimeEvent, MarketEvent, RuntimeEvent, SystemRuntimeEvent


class RuntimeComponent(Protocol):
    """Small runtime-owned projection that publishes exactly one view."""

    @property
    def key(self) -> str:
        ...

    @property
    def schema(self) -> ViewSchema:
        ...

    def on_event(self, event: RuntimeEvent) -> None:
        ...

    def view(self) -> object:
        ...


@dataclass(frozen=True, slots=True)
class MarketCurrentView:
    event_count: int = 0
    last_stream: str | None = None
    last_sequence: int | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


class MarketCurrentProjection:
    key = "market.current"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "consumed market event count", "runtime sequence", "event source"),
            ViewFieldSchema("last_stream", "latest market stream", "event time", "event source"),
            ViewFieldSchema("last_sequence", "latest market event sequence", "event order", "event source"),
            ViewFieldSchema("last_event_time", "latest market event time", "event time", "event source"),
            ViewFieldSchema("last_payload", "latest canonical market payload", "event time", "event source"),
        ),
        mutability="runtime_writable",
        evidence="market event projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: MarketEvent | None = None

    def on_event(self, event: RuntimeEvent) -> None:
        if isinstance(event, MarketEvent):
            self._event_count += 1
            self._last_event = event

    def view(self) -> MarketCurrentView:
        if self._last_event is None:
            return MarketCurrentView(event_count=self._event_count)
        return MarketCurrentView(
            event_count=self._event_count,
            last_stream=self._last_event.stream,
            last_sequence=self._last_event.sequence,
            last_event_time=self._last_event.time,
            last_payload=dict(self._last_event.payload),
        )


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
    projection: AccountProjection | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    initial_equity: Decimal | None = None
    net_profit: Decimal | None = None
    total_return: Decimal | None = None
    payload: Mapping[str, object] | None = None


class AccountCurrentProjection:
    """Runtime projection for account events across backtest, simulation, paper, and live."""

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
        self._last_event: AccountRuntimeEvent | None = None
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
                ViewFieldSchema("balances", "account balances", "event time", "account snapshot or projection"),
                ViewFieldSchema("margins", "account margin states", "event time", "account projection"),
                ViewFieldSchema("positions", "account positions", "event time", "account snapshot or projection"),
                ViewFieldSchema("open_orders", "venue open orders", "event time", "account projection"),
                ViewFieldSchema("pending_orders", "local active order states", "runtime state", "order journal"),
                ViewFieldSchema("stale", "account projection staleness flag", "event time", "account projection"),
                ViewFieldSchema("projection", "complete account projection", "event time", "account projection"),
                ViewFieldSchema("cash", "cash in selected equity currency", "event time", "account snapshot or projection"),
                ViewFieldSchema("equity", "marked account equity", "event time", "account event"),
                ViewFieldSchema("initial_equity", "first or configured account equity baseline", "run baseline", "runtime"),
                ViewFieldSchema("net_profit", "equity minus baseline", "event time", "runtime account projection"),
                ViewFieldSchema("total_return", "net profit divided by baseline", "event time", "runtime account projection"),
                ViewFieldSchema("payload", "source account event payload", "event time", "account event"),
            ),
            mutability="runtime_writable",
            evidence="runtime account event projection",
        )

    def on_event(self, event: RuntimeEvent) -> None:
        if not isinstance(event, AccountRuntimeEvent) or event.context != self.context:
            return
        self._event_count += 1
        self._last_event = event
        equity = self._equity(event)
        if equity is not None:
            if self._initial_equity is None:
                self._initial_equity = equity
            self._last_equity = equity

    def view(self) -> AccountCurrentView:
        if self._last_event is None:
            return AccountCurrentView(context=self.context, initial_equity=self._initial_equity)

        event = self._last_event
        balances = self._balances(event)
        projection = event.projection
        cash = self._cash(balances)
        equity = self._last_equity
        initial = self._initial_equity
        net_profit = None if equity is None or initial is None else equity - initial
        total_return = None if net_profit is None or initial in (None, Decimal("0")) else net_profit / initial
        return AccountCurrentView(
            context=self.context,
            event_count=self._event_count,
            last_event_time=event.time,
            source=self._source(event),
            balances=balances,
            margins=self._margins(event),
            positions=self._positions(event),
            open_orders=() if projection is None else projection.open_orders,
            pending_orders=() if projection is None else projection.pending_orders,
            stale=False if projection is None else projection.stale,
            projection=projection,
            cash=cash,
            equity=equity,
            initial_equity=initial,
            net_profit=net_profit,
            total_return=total_return,
            payload=dict(event.payload or {}),
        )

    def _balances(self, event: AccountRuntimeEvent) -> tuple[AccountBalance, ...]:
        if event.projection is not None:
            return event.projection.balances
        if event.snapshot is not None:
            return event.snapshot.balances
        return ()

    def _margins(self, event: AccountRuntimeEvent) -> tuple[MarginState, ...]:
        if event.projection is not None:
            return event.projection.margins
        if event.snapshot is not None:
            return event.snapshot.margins
        return ()

    def _positions(self, event: AccountRuntimeEvent) -> tuple[PositionSnapshot, ...]:
        if event.projection is not None:
            return event.projection.positions
        if event.snapshot is not None:
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

    def _equity(self, event: AccountRuntimeEvent) -> Decimal | None:
        if "equity" in event.payload:
            return Decimal(str(event.payload["equity"]))
        balances = self._balances(event)
        cash = self._cash(balances)
        if cash is None:
            return None
        if "unrealized_pnl" in event.payload:
            return cash + Decimal(str(event.payload["unrealized_pnl"]))
        return cash

    def _source(self, event: AccountRuntimeEvent) -> AccountSource | str | None:
        if event.projection is not None:
            return event.projection.source
        if event.snapshot is not None:
            return event.snapshot.source
        value = event.payload.get("source")
        return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class SystemEventView:
    event_count: int = 0
    last_name: str | None = None
    last_stream: str | None = None
    last_event_time: datetime | None = None
    last_payload: dict[str, object] | None = None


class SystemEventProjection:
    key = "system.events"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("event_count", "consumed system event count", "runtime sequence", "system event"),
            ViewFieldSchema("last_name", "latest system event name", "event time", "system event"),
            ViewFieldSchema("last_stream", "latest system event stream", "event time", "system event"),
            ViewFieldSchema("last_event_time", "latest system event time", "event time", "system event"),
            ViewFieldSchema("last_payload", "latest system event payload", "event time", "system event"),
        ),
        mutability="runtime_writable",
        evidence="runtime system event projection",
    )

    def __init__(self) -> None:
        self._event_count = 0
        self._last_event: SystemRuntimeEvent | None = None

    def on_event(self, event: RuntimeEvent) -> None:
        if isinstance(event, SystemRuntimeEvent):
            self._event_count += 1
            self._last_event = event

    def view(self) -> SystemEventView:
        if self._last_event is None:
            return SystemEventView(event_count=self._event_count)
        return SystemEventView(
            event_count=self._event_count,
            last_name=self._last_event.name,
            last_stream=self._last_event.stream,
            last_event_time=self._last_event.time,
            last_payload=dict(self._last_event.payload),
        )


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


__all__ = [
    "AccountCurrentProjection",
    "AccountCurrentView",
    "MarketCurrentProjection",
    "MarketCurrentView",
    "RuntimeComponent",
    "SystemEventProjection",
    "SystemEventView",
]
