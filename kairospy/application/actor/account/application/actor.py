"""Account Actor application facade for account and execution usecases."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Mapping

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.domain.account import AccountBookRef, AccountContext, AccountSnapshot, AccountState
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.intent import IntentEvent, IntentEventKind, IntentJournal, IntentState, TradeIntent
from kairospy.domain.order import OrderState
from kairospy.application.usecases.execution.application.component import (
    CancelOrderCommand as ExecutionCancelOrderCommand,
    ExecutionIntentPreparation,
    ExecuteIntentCommand as ExecutionExecuteIntentCommand,
)
from kairospy.application.actor.risk.application.commands import (
    ConsumeRiskCommand,
    ReleaseRiskCommand,
    ReserveRiskCommand,
)
from kairospy.application.usecases.risk.application.budget import RiskReservationRequest
from .commands import (
    AccountMarketProfileUpdated,
    CancelIntentCommand,
    CancelOrderCommand,
    ExecuteIntentCommand,
    RecordIntentsCommand,
    RefreshAccountMarketProfileCommand,
)


_LOGGER = logging.getLogger("kairospy.actor.account")


class AccountActor(BusinessActor):
    def __init__(self, source: object, bus: MessageBus, *, execution_source: object | None = None, account_application: object | None = None, execution_application: object | None = None, intents: IntentJournal | None = None, connections: object | None = None, publish_connection_health: object | None = None, projectors: object | None = None, risk_actor: object | None = None) -> None:
        super().__init__("account", bus=bus)
        self.runtime = source
        self._intents = intents or IntentJournal()
        self.account_application = account_application
        self.execution_application = execution_application
        self.projectors = projectors
        self.execution_runtime = execution_source
        self.risk_actor = risk_actor
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

    def refresh_market_profile(self, command: RefreshAccountMarketProfileCommand) -> AccountMarketProfile | None:
        application = self.account_application
        refresh = None if application is None else getattr(application, "market_profile", None)
        if not callable(refresh):
            raise RuntimeError("account actor has no account market profile usecase")
        profile = refresh(command.account, command.market, at=command.at, refresh=True)
        if profile is not None:
            update = getattr(application, "update_market_profile", None)
            if callable(update):
                update(profile)
        return profile

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

    async def dispatch_command(self, command: object) -> object:
        """Execute an account command through the actor mailbox."""
        now = getattr(command, "at", None) or datetime.now(timezone.utc)
        context = getattr(command, "context", None)
        causation_id = getattr(getattr(context, "event", None), "message_id", None)
        sequence = self._next_command_sequence()
        return await self.ask(
            Message(
                "account.command",
                command,
                now if isinstance(now, datetime) else datetime.now(timezone.utc),
                "account.actor",
                sequence,
                message_id=f"account-command-{sequence}",
                correlation_id=_command_correlation(command),
                causation_id=causation_id,
                command_id=getattr(command, "command_id", None),
            )
        )

    def apply_command(self, command: object) -> object:
        """Apply a command for the deterministic synchronous System API.

        Long-running sessions use ``dispatch_command`` and the mailbox.  The
        synchronous ``System.process`` API has no running actor loop, but it
        still enters the same command boundary and handler.
        """
        if isinstance(command, RecordIntentsCommand):
            self.record_intents(command.intents, at=command.at)
            return None
        if isinstance(command, ExecuteIntentCommand):
            return self.execute_intent(command.intent, command.context)
        if isinstance(command, CancelOrderCommand):
            return self.cancel_order(command.order_id, at=command.at)
        if isinstance(command, CancelIntentCommand):
            return self.cancel_intent(command.intent_id, at=command.at)
        raise TypeError(f"unsupported account command: {type(command).__name__}")

    def _next_command_sequence(self) -> int:
        sequence = getattr(self, "_command_sequence", 0) + 1
        self._command_sequence = sequence
        return sequence

    def execute_intent(self, intent: TradeIntent, context: object) -> object:
        application = self.execution_runtime or self.execution_application
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
        if application is self.execution_runtime:
            return execute(intent, execution_context)
        return execute(
            ExecutionExecuteIntentCommand(
                intent=intent,
                context=execution_context,
                account=account,
                current_quantity=current_quantity,
                account_snapshot=self.snapshot(account.book),
            )
        )

    def cancel_order(self, order_id: str, *, at: object) -> OrderState:
        application = self.execution_runtime or self.execution_application
        cancel = None if application is None else getattr(application, "cancel_order", None)
        if not callable(cancel):
            raise RuntimeError("account actor has no order cancellation usecase")
        if application is self.execution_runtime:
            return cancel(str(order_id), at=at)
        return cancel(ExecutionCancelOrderCommand(str(order_id), at))

    def cancel_intent(self, intent_id: object, *, at: object) -> IntentState:
        state = self._intents.get(str(intent_id))
        if state.status.terminal:
            return state
        for order_id in state.order_ids:
            order = self._order(order_id)
            if order is not None and not order.status.terminal:
                self.cancel_order(order_id, at=at)
        return self._intents.record(
            IntentEvent(state.intent.intent_id, IntentEventKind.CANCELED, at, order_ids=state.order_ids)
        )

    def _order(self, order_id: str) -> OrderState | None:
        application = self.execution_application
        orders = None if application is None else getattr(application, "orders", None)
        if not callable(orders):
            return None
        try:
            return next((order for order in orders() if order.order_id == order_id), None)
        except (KeyError, LookupError):
            return None

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
        _LOGGER.info(
            "actor=account phase=prepare connections=%s execution_source=%s",
            self._connections is not None,
            self.execution_runtime is not None,
        )
        self._start_connections()
        events = getattr(self.runtime, "events", None)
        if callable(events):
            self.start_event_loop(events(), is_finite=bool(getattr(self.runtime, "is_finite", False)), name="account")
        execution_events = getattr(self.execution_runtime, "events", None)
        if callable(execution_events):
            self.start_event_loop(execution_events(), is_finite=bool(getattr(self.execution_runtime, "is_finite", False)), name="execution")
        _LOGGER.info("actor=account phase=streaming loops=%d", len(self._event_tasks))

    async def on_stop(self) -> None:
        _LOGGER.info("actor=account phase=stopping")
        self._stop_connections()

    async def process(self, message: Message) -> object:
        """Apply account-owned stream events to the account state boundary."""
        payload = message.payload
        if message.topic == "account.command":
            if isinstance(payload, ExecuteIntentCommand) and self.risk_actor is not None:
                return await self.execute_intent_async(payload.intent, payload.context)
            if isinstance(payload, CancelOrderCommand) and self.risk_actor is not None:
                return await self.cancel_order_async(payload.order_id, at=payload.at)
            return self.apply_command(payload)
        elif message.topic == "account.snapshot" and isinstance(payload, AccountSnapshot):
            self.update_snapshot(payload)
        elif message.topic == "account.market_profile.refresh":
            if isinstance(payload, RefreshAccountMarketProfileCommand):
                profile = self.refresh_market_profile(payload)
                if profile is not None and self.bus is not None:
                    await self.bus.publish(
                        Message(
                            "account.market_profile.updated",
                            AccountMarketProfileUpdated(profile),
                            datetime.now(timezone.utc),
                            "account.actor",
                            1,
                        )
                    )
        elif message.topic == "execution.update":
            update = payload
            if isinstance(payload, Mapping):
                update = payload.get("update")
            if isinstance(update, ExecutionUpdate):
                state = self.apply_execution_update(update)
                if self.risk_actor is not None and state.status.terminal:
                    reservation_id = state.request.reservation_id or state.request.order_id
                    if state.status.value == "filled":
                        await self._dispatch_risk(ConsumeRiskCommand(reservation_id))
                    elif state.status.value in {"canceled", "rejected", "expired"}:
                        await self._dispatch_risk(ReleaseRiskCommand(reservation_id))
        elif message.domain == "market":
            on_market_event = getattr(self.execution_runtime, "on_market_event", None)
            if callable(on_market_event):
                on_market_event(payload)
        projector_event = getattr(self.projectors, "on_event", None)
        if callable(projector_event):
            projector_event(message)
        return None

    async def execute_intent_async(self, intent: TradeIntent, context: object) -> object:
        """Reserve risk before entering the execution usecase."""
        application = self.execution_application
        prepare = None if application is None else getattr(application, "prepare_intent", None)
        execute = None if application is None else getattr(application, "execute_prepared_intent", None)
        if not callable(prepare) or not callable(execute):
            return self.execute_intent(intent, context)
        accounts = self.accounts()
        account = _resolve_intent_account(accounts, intent)
        if account is None:
            return None
        quantity = getattr(application, "current_quantity", None)
        current_quantity = Decimal("0") if not callable(quantity) else quantity(account.book, intent.instrument_id)
        execution_context = context
        if getattr(context, "intents", None) is not self._intents:
            execution_context = SimpleNamespace(now=getattr(context, "now", None), intents=self._intents)
        preparation = prepare(
            ExecutionExecuteIntentCommand(
                intent=intent,
                context=execution_context,
                account=account,
                current_quantity=current_quantity,
                account_snapshot=self.snapshot(account.book),
            ),
            check_safety=False,
        )
        if not isinstance(preparation, ExecutionIntentPreparation) or preparation.plan is None:
            return self.execute_intent(intent, execution_context)
        if preparation.risk_request is not None and self.risk_actor is not None:
            try:
                await self._dispatch_risk(
                    ReserveRiskCommand(
                        RiskReservationRequest(
                            preparation.risk_reservation_id or preparation.plan.request.order_id,
                            preparation.risk_request,
                        )
                    ),
                    correlation_id=str(intent.intent_id),
                    causation_id=None,
                )
            except (KeyError, ValueError) as error:
                reject = getattr(application, "reject_prepared_intent", None)
                if callable(reject):
                    reject(preparation, f"risk budget: {error}")
                return None
        try:
            runtime_execute = getattr(self.execution_runtime, "execute_intent", None)
            if callable(runtime_execute):
                result = runtime_execute(intent, execution_context)
            else:
                result = execute(
                    preparation,
                    risk_reserved=self.risk_actor is not None,
                    events_already_recorded=False,
                )
        except BaseException:
            if preparation.risk_reservation_id and self.risk_actor is not None:
                await self._dispatch_risk(ReleaseRiskCommand(preparation.risk_reservation_id), correlation_id=str(intent.intent_id))
            raise
        if preparation.risk_reservation_id and self.risk_actor is not None:
            status = getattr(result, "status", None)
            if getattr(status, "value", status) in {"rejected", "canceled", "expired"}:
                await self._dispatch_risk(ReleaseRiskCommand(preparation.risk_reservation_id), correlation_id=str(intent.intent_id))
        return result

    async def cancel_order_async(self, order_id: str, *, at: object) -> OrderState:
        result = self.cancel_order(order_id, at=at)
        if self.risk_actor is not None and result.status.value in {"canceled", "rejected", "expired"}:
            await self._dispatch_risk(ReleaseRiskCommand(result.request.reservation_id or result.request.order_id), correlation_id=result.request.reservation_id or result.request.order_id)
        return result

    async def _dispatch_risk(self, command: object, *, correlation_id: str | None = None, causation_id: str | None = None) -> object:
        dispatch = getattr(self.risk_actor, "dispatch_command", None)
        if not callable(dispatch):
            raise RuntimeError("account actor has no risk actor command gateway")
        return await dispatch(command, correlation_id=correlation_id, causation_id=causation_id)

__all__ = [
    "AccountActor",
    "AccountMarketProfileUpdated",
    "CancelIntentCommand",
    "CancelOrderCommand",
    "ExecuteIntentCommand",
    "RecordIntentsCommand",
    "RefreshAccountMarketProfileCommand",
]


def _command_correlation(command: object) -> str | None:
    intent = getattr(command, "intent", None)
    intent_id = getattr(intent, "intent_id", None)
    if intent_id:
        return str(intent_id)
    intent_id = getattr(command, "intent_id", None)
    if intent_id:
        return str(intent_id)
    order_id = getattr(command, "order_id", None)
    return None if order_id is None else str(order_id)


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
