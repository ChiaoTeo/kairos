"""Account Actor application facade for account and execution usecases."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from types import SimpleNamespace
from typing import Mapping

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.domain.account import AccountBookRef, AccountContext, AccountSnapshot, AccountState
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.intent import IntentJournal, TradeIntent
from kairospy.domain.order import OrderState
from kairospy.application.usecases.execution.application.component import ExecuteIntentCommand


class AccountActor(BusinessActor):
    def __init__(self, source: object, bus: MessageBus, *, execution_source: object | None = None, account_application: object | None = None, execution_application: object | None = None, intents: IntentJournal | None = None, connections: object | None = None, publish_connection_health: object | None = None, projectors: object | None = None) -> None:
        super().__init__("account", bus=bus)
        self.runtime = source
        self._intents = intents or IntentJournal()
        self.account_application = account_application
        self.execution_application = execution_application
        self.projectors = projectors
        self.execution_runtime = execution_source
        self.is_finite = bool(getattr(source, "is_finite", False)) or bool(getattr(execution_source, "is_finite", False))
        self._connections = connections
        self._publish_connection_health = publish_connection_health if callable(publish_connection_health) else None
        self._connection_roles = ("account_broker", "account_private_stream", "account_or_execution", "execution")

    @property
    def intents(self) -> IntentJournal:
        return self._intents

    def accounts(self) -> tuple[AccountContext, ...]:
        application = self.account_application
        if application is None:
            return ()
        accounts = getattr(application, "accounts", None)
        return () if not callable(accounts) else tuple(accounts())

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        application = self.account_application
        snapshot = None if application is None else getattr(application, "snapshot", None)
        return None if not callable(snapshot) else snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        application = self.account_application
        state = None if application is None else getattr(application, "state", None)
        return None if not callable(state) else state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        application = self.account_application
        update = None if application is None else getattr(application, "update_snapshot", None)
        if not callable(update):
            raise RuntimeError("account actor has no account snapshot usecase")
        update(snapshot)

    def apply_execution_update(self, update: ExecutionUpdate) -> OrderState:
        application = self.execution_application
        apply_update = None if application is None else getattr(application, "apply_update", None)
        if not callable(apply_update):
            raise RuntimeError("account actor has no execution update usecase")
        return apply_update(update)

    def orders(self, account: AccountBookRef | None = None) -> tuple[OrderState, ...]:
        application = self.execution_application
        orders = None if application is None else getattr(application, "orders", None)
        return () if not callable(orders) else tuple(orders(account))

    def record_intent(self, intent: object, *, at: object) -> None:
        self._intents.record_intent(intent, at=at)  # type: ignore[arg-type]

    def record_intents(self, intents: Iterable[object], *, at: object) -> None:
        for intent in intents:
            self.record_intent(intent, at=at)

    def execute_intent(self, intent: TradeIntent, context: object) -> object:
        application = self.execution_application
        execute = None if application is None else getattr(application, "execute_intent", None)
        if not callable(execute):
            return None
        accounts = self.accounts()
        account = _resolve_intent_account(accounts, intent)
        if account is None:
            return None
        quantity = getattr(application, "current_quantity", None)
        current_quantity = Decimal("0") if not callable(quantity) else quantity(account.book, intent.instrument_id)
        execution_context = context
        if getattr(context, "intents", None) is not self._intents:
            execution_context = SimpleNamespace(now=getattr(context, "now", None), intents=self._intents)
        return execute(
            ExecuteIntentCommand(
                intent=intent,
                context=execution_context,
                account=account,
                current_quantity=current_quantity,
                account_snapshot=self.snapshot(account.book),
            )
        )

    def _start_connections(self) -> None:
        manager = self._connections
        if manager is None:
            return
        start_roles = getattr(manager, "start_roles", None)
        if callable(start_roles):
            start_roles(self._connection_roles)
        health = getattr(manager, "health", None)
        if callable(health) and self._publish_connection_health is not None:
            self._publish_connection_health(health())

    def _stop_connections(self) -> None:
        manager = self._connections
        if manager is None:
            return
        stop_roles = getattr(manager, "stop_roles", None)
        if callable(stop_roles):
            stop_roles(self._connection_roles)

    async def on_start(self) -> None:
        self._start_connections()
        events = getattr(self.runtime, "events", None)
        if callable(events):
            self.start_event_loop(events(), is_finite=bool(getattr(self.runtime, "is_finite", False)), name="account")
        execution_events = getattr(self.execution_runtime, "events", None)
        if callable(execution_events):
            self.start_event_loop(execution_events(), is_finite=bool(getattr(self.execution_runtime, "is_finite", False)), name="execution")

    async def on_stop(self) -> None:
        self._stop_connections()

    async def process(self, message: Message) -> None:
        """Apply account-owned stream events to the account state boundary."""
        payload = message.payload
        if message.topic == "account.snapshot" and isinstance(payload, AccountSnapshot):
            self.update_snapshot(payload)
        elif message.topic == "execution.update":
            update = payload
            if isinstance(payload, Mapping):
                update = payload.get("update")
            if isinstance(update, ExecutionUpdate):
                self.apply_execution_update(update)
        projector_event = getattr(self.projectors, "on_event", None)
        if callable(projector_event):
            projector_event(message)

__all__ = ["AccountActor"]


def _resolve_intent_account(accounts: tuple[AccountContext, ...], intent: TradeIntent) -> AccountContext | None:
    if not accounts:
        return None
    if intent.account_index is not None:
        try:
            return accounts[intent.account_index]
        except IndexError:
            return None
    for account in accounts:
        if intent.account_id is not None and account.book.account_id == intent.account_id:
            if intent.account_book is None or str(account.book.book) == intent.account_book:
                return account
        if intent.account_book is not None and account.book.book_key == intent.account_book:
            return account
    return accounts[0]
