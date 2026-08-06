"""ExternalAccount Actor application facade for account and execution usecases."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Mapping

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.domain.account import AccountCapability, AccountRuntimeContext, AccountCurrentView, AccountDetailView, AccountFeeSchedule, AccountMarketProfile, AccountSegment, AccountSnapshot, AccountState, AssetCode
from kairospy.domain.market import MarketEvent, MarketEventValue
from kairospy.application.usecases.account.application.directory import AccountDirectory
from kairospy.application.usecases.account.application.read import AccountQueryResult, AccountRefreshRequest, AccountRefreshResult
from kairospy.application.usecases.account.application.runtime_capability import AccountRuntimeCapability
from .ports import AccountActorViewPort, AccountConnectionManager, AccountEventSource, AccountProjectionPort, AccountProjectorPort, ExecutionEventSource, RiskCommand, RiskCommandPort, RiskCommandResult
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.intent import IntentEvent, IntentEventKind, IntentJournal, IntentState, TradeIntent
from kairospy.domain.order import OrderState
from kairospy.application.usecases.account.application.view_contracts import AccountViewObservation
from kairospy.application.usecases.execution.application.component import (
    CancelOrderCommand as ExecutionCancelOrderCommand,
    ExecutionApplication,
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
    QueryAccountCommand,
    RefreshAccountCommand,
    AccountCommand,
    AccountCommandResult,
    IntentExecutionContext,
)


_LOGGER = logging.getLogger("kairospy.actor.account")


class AccountActor(BusinessActor):
    def __init__(self, source: AccountEventSource | None, bus: MessageBus | None, *, execution_source: ExecutionEventSource | None = None, account_application: AccountRuntimeCapability | None = None, execution_application: ExecutionApplication | None = None, intents: IntentJournal | None = None, connections: AccountConnectionManager | None = None, publish_connection_health: Callable[[Mapping[str, object]], None] | None = None, projectors: AccountProjectorPort | None = None, risk_actor: RiskCommandPort | None = None, account_view: AccountActorViewPort | None = None) -> None:
        super().__init__("account", bus=bus)
        self.runtime = source
        self._intents = intents or IntentJournal()
        self.account_application = account_application
        self.account_view = account_view
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

    def accounts(self) -> tuple[AccountRuntimeContext, ...]:
        application = self.account_application
        if application is None:
            return ()
        return tuple(application.accounts())

    def _view(self) -> AccountActorViewPort:
        if self.account_view is None:
            raise RuntimeError("account actor has no account view projection")
        return self.account_view

    def directory(self) -> AccountDirectory:
        return self._view().directory()

    def capabilities(self) -> tuple[AccountCapability, ...]:
        return self._view().capabilities()

    def fees(self) -> tuple[AccountFeeSchedule, ...]:
        return self._view().fees()

    def market_profiles(self) -> tuple[AccountMarketProfile, ...]:
        return self._view().market_profiles()

    def current_view(self, context: AccountRuntimeContext, *, event_count: int = 0, last_event_time: datetime | None = None, payload: AccountViewObservation | None = None, equity_currency: AssetCode | str | None = None, latest_equity: Decimal | None = None, initial_equity: Decimal | None = None, pending_orders: tuple[OrderState, ...] = (), now: datetime | None = None) -> AccountCurrentView:
        return self._view().current_view(context, event_count=event_count, last_event_time=last_event_time, payload=payload, equity_currency=equity_currency, latest_equity=latest_equity, initial_equity=initial_equity, pending_orders=pending_orders, now=now)

    def detail_view(self, context: AccountRuntimeContext, *, event_count: int = 0, last_event_time: datetime | None = None, metadata: dict[str, object] | None = None, now: datetime | None = None) -> AccountDetailView:
        return self._view().detail_view(context, event_count=event_count, last_event_time=last_event_time, metadata=metadata, now=now)

    @property
    def projection(self) -> AccountProjectionPort | None:
        return self._view().projection

    @property
    def account(self) -> AccountRuntimeContext:
        return self._view().account

    @property
    def valuation_asset(self) -> AssetCode:
        return self._view().valuation_asset

    @property
    def settlement_asset(self) -> AssetCode:
        return self._view().settlement_asset

    def asset_balance(self, currency: AssetCode | str | None = None) -> Decimal:
        return self._view().asset_balance(currency)

    def positions(self) -> dict[str, Decimal]:
        return self._view().positions()

    def record_funding(
        self,
        *,
        occurred_at: datetime,
        currency: AssetCode | str,
        balance_delta: Decimal,
        instrument_id: str,
        reference_id: str,
    ) -> None:
        self._view().record_funding(
            occurred_at=occurred_at,
            currency=currency,
            balance_delta=balance_delta,
            instrument_id=instrument_id,
            reference_id=reference_id,
        )

    def snapshot(self, account: AccountSegment | None = None) -> AccountSnapshot | None:
        application = self.account_application
        return None if application is None else application.snapshot(account)

    def state(self, account: AccountSegment | None = None) -> AccountState | None:
        application = self.account_application
        return None if application is None else application.state(account)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        application = self.account_application
        if application is None:
            raise RuntimeError("account actor has no account snapshot usecase")
        application.update_snapshot(snapshot)
        execution = self.execution_application
        reflect = None if execution is None else getattr(execution, "reflect_account_snapshot", None)
        if callable(reflect):
            reflect(snapshot)

    def query(self, command: QueryAccountCommand) -> AccountQueryResult:
        application = self.account_application
        if application is None:
            raise RuntimeError("account actor has no account query usecase")
        return application.query(command.request)

    def refresh(self, command: RefreshAccountCommand) -> AccountRefreshResult:
        application = self.account_application
        if application is None:
            raise RuntimeError("account actor has no account refresh usecase")
        return application.refresh(command.request)

    def refresh_market_profile(self, command: RefreshAccountMarketProfileCommand) -> AccountMarketProfile | None:
        application = self.account_application
        if application is None:
            raise RuntimeError("account actor has no account market profile usecase")
        profile = application.market_profile(command.account, command.market, at=command.at, refresh=True)
        if profile is not None:
            application.update_market_profile(profile)
        return profile

    def apply_execution_update(self, update: ExecutionUpdate) -> OrderState:
        application = self.execution_application
        apply_update = None if application is None else getattr(application, "apply_update", None)
        if not callable(apply_update):
            raise RuntimeError("account actor has no execution update usecase")
        return apply_update(update)

    def orders(self, account: AccountSegment | None = None) -> tuple[OrderState, ...]:
        application = self.execution_application
        orders = None if application is None else getattr(application, "orders", None)
        return () if not callable(orders) else tuple(orders(account))

    def record_intent(self, intent: TradeIntent, *, at: datetime) -> None:
        self._intents.record_intent(intent, at=at)  # type: ignore[arg-type]

    def record_intents(self, intents: Iterable[TradeIntent], *, at: datetime) -> None:
        for intent in intents:
            self.record_intent(intent, at=at)

    async def dispatch_command(self, command: AccountCommand) -> AccountCommandResult:
        """Execute an account command through the actor mailbox."""
        now = _command_time(command)
        context = command.context if isinstance(command, ExecuteIntentCommand) else None
        causation_id = getattr(getattr(context, "event", None), "message_id", None)
        sequence = self._next_command_sequence()
        return await self.ask(
            Message(
                "account.command",
                command,
                now,
                "account.actor",
                sequence,
                message_id=f"account-command-{sequence}",
                correlation_id=_command_correlation(command),
                causation_id=causation_id,
                command_id=None,
            )
        )

    def apply_command(self, command: AccountCommand) -> AccountCommandResult:
        """Apply a command for the deterministic synchronous System API.

        Long-running sessions use ``dispatch_command`` and the mailbox.  The
        synchronous ``System.process`` API has no running actor loop, but it
        still enters the same command boundary and handler.
        """
        if isinstance(command, RecordIntentsCommand):
            self.record_intents(command.intents, at=command.at)
            return None
        if isinstance(command, QueryAccountCommand):
            return self.query(command)
        if isinstance(command, RefreshAccountCommand):
            return self.refresh(command)
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

    def execute_intent(self, intent: TradeIntent, context: IntentExecutionContext) -> OrderState | None:
        application = self.execution_runtime or self.execution_application
        execute = None if application is None else getattr(application, "execute_intent", None)
        if not callable(execute):
            return None
        accounts = self.accounts()
        account = _resolve_intent_account(accounts, intent)
        if account is None:
            return None
        quantity = getattr(application, "current_quantity", None)
        current_quantity = Decimal("0") if not callable(quantity) else quantity(account.segment, intent.instrument_id)
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
                account_snapshot=self.snapshot(account.segment),
            )
        )

    def cancel_order(self, order_id: str, *, at: datetime) -> OrderState:
        application = self.execution_runtime or self.execution_application
        cancel = None if application is None else getattr(application, "cancel_order", None)
        if not callable(cancel):
            raise RuntimeError("account actor has no order cancellation usecase")
        if application is self.execution_runtime:
            return cancel(str(order_id), at=at)
        return cancel(ExecutionCancelOrderCommand(str(order_id), at))

    def cancel_intent(self, intent_id: str, *, at: datetime) -> IntentState:
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
        manager.start_roles(self._connection_roles)
        if self._publish_connection_health is not None:
            self._publish_connection_health(manager.health())

    def _stop_connections(self) -> None:
        manager = self._connections
        if manager is None:
            return
        manager.stop_roles(self._connection_roles)

    async def on_start(self) -> None:
        _LOGGER.info(
            "actor=account phase=prepare connections=%s execution_source=%s",
            self._connections is not None,
            self.execution_runtime is not None,
        )
        self._start_connections()
        if self.runtime is not None:
            events = getattr(self.runtime, "events", None)
            if callable(events):
                self.start_event_loop(events(), is_finite=bool(getattr(self.runtime, "is_finite", False)), name="account")
        if self.execution_runtime is not None:
            events = getattr(self.execution_runtime, "events", None)
            if callable(events):
                self.start_event_loop(events(), is_finite=bool(getattr(self.execution_runtime, "is_finite", False)), name="execution")
        _LOGGER.info("actor=account phase=streaming loops=%d", len(self._event_tasks))

    async def on_stop(self) -> None:
        _LOGGER.info("actor=account phase=stopping")
        self._stop_connections()

    async def process(self, message: Message) -> AccountCommandResult:
        """Apply account-owned stream events to the account state boundary."""
        payload = message.payload
        if message.topic == "account.command":
            if isinstance(payload, ExecuteIntentCommand) and self.risk_actor is not None:
                return await self.execute_intent_async(payload.intent, payload.context)
            if isinstance(payload, CancelOrderCommand) and self.risk_actor is not None:
                return await self.cancel_order_async(payload.order_id, at=payload.at)
            result = self.apply_command(payload)
            if isinstance(result, AccountRefreshResult):
                await self._publish_refreshed_snapshot(result, message)
            return result
        elif message.topic == "account.refresh.requested":
            request = payload.request if isinstance(payload, RefreshAccountCommand) else payload
            if isinstance(request, AccountRefreshRequest):
                result = self.refresh(RefreshAccountCommand(request))
                await self._publish_refreshed_snapshot(result, message)
                return result
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
            if self.execution_runtime is not None and isinstance(payload, (MarketEvent, MarketEventValue)):
                self.execution_runtime.on_market_event(payload)
        if self.projectors is not None:
            self.projectors.on_event(message)
        return None

    async def _publish_refreshed_snapshot(self, result: AccountRefreshResult, caused_by: Message) -> None:
        if self.bus is None:
            return
        sequence = self._next_command_sequence()
        await self.bus.publish(
            Message(
                "account.snapshot",
                result.read.snapshot,
                result.read.snapshot.observed_at or datetime.now(timezone.utc),
                "account.actor",
                sequence,
                message_id=f"account-refresh-{sequence}",
                correlation_id=caused_by.correlation_id,
                causation_id=caused_by.message_id,
            )
        )

    async def execute_intent_async(self, intent: TradeIntent, context: IntentExecutionContext) -> OrderState | None:
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
        current_quantity = Decimal("0") if not callable(quantity) else quantity(account.segment, intent.instrument_id)
        execution_context = context
        if getattr(context, "intents", None) is not self._intents:
            execution_context = SimpleNamespace(now=getattr(context, "now", None), intents=self._intents)
        preparation = prepare(
            ExecutionExecuteIntentCommand(
                intent=intent,
                context=execution_context,
                account=account,
                current_quantity=current_quantity,
                account_snapshot=self.snapshot(account.segment),
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

    async def cancel_order_async(self, order_id: str, *, at: datetime) -> OrderState:
        result = self.cancel_order(order_id, at=at)
        if self.risk_actor is not None and result.status.value in {"canceled", "rejected", "expired"}:
            await self._dispatch_risk(ReleaseRiskCommand(result.request.reservation_id or result.request.order_id), correlation_id=result.request.reservation_id or result.request.order_id)
        return result

    async def _dispatch_risk(self, command: RiskCommand, *, correlation_id: str | None = None, causation_id: str | None = None) -> RiskCommandResult:
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
    "QueryAccountCommand",
    "RefreshAccountCommand",
    "RefreshAccountMarketProfileCommand",
]


def _command_time(command: AccountCommand) -> datetime:
    if isinstance(command, (RecordIntentsCommand, CancelOrderCommand, CancelIntentCommand)):
        return command.at
    if isinstance(command, RefreshAccountCommand):
        return command.request.at or datetime.now(timezone.utc)
    if isinstance(command, QueryAccountCommand):
        return command.request.now or datetime.now(timezone.utc)
    if isinstance(command, ExecuteIntentCommand):
        return command.context.now or datetime.now(timezone.utc)
    return command.at or datetime.now(timezone.utc)


def _command_correlation(command: AccountCommand) -> str | None:
    if isinstance(command, ExecuteIntentCommand):
        return str(command.intent.intent_id)
    if isinstance(command, CancelIntentCommand):
        return command.intent_id
    if isinstance(command, CancelOrderCommand):
        return command.order_id
    return None


def _resolve_intent_account(accounts: tuple[AccountRuntimeContext, ...], intent: TradeIntent) -> AccountRuntimeContext | None:
    if not accounts:
        return None
    if intent.account_index is not None:
        try:
            return accounts[intent.account_index]
        except IndexError:
            return None
    for account in accounts:
        if intent.account_id is not None and account.segment.account_id == intent.account_id:
            if intent.account_segment is None or account.segment.segment_id == intent.account_segment or str(account.segment.product_family) == intent.account_segment:
                return account
        if intent.account_segment is not None and account.segment.key == intent.account_segment:
            return account
    return accounts[0]
