from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from kairospy.application.actor.account.application.actor import AccountActor, RecordIntentsCommand
from kairospy.application.actor.account.application.commands import ExecuteIntentCommand
from kairospy.application.actor.risk.application import (
    AssessRiskCommand,
    ReserveRiskCommand,
    RiskActor,
)
from kairospy.application.usecases.execution.application.component import ExecutionApplication
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
from kairospy.application.usecases.risk.application.budget import RiskApplication, RiskAssessmentRequest, RiskReservationRequest
from kairospy.application.usecases.risk.domain import BudgetRef, RiskBudget, RiskMetric, RiskUsage
from kairospy.domain.account import AccountBookRef, AccountContext, Environment
from kairospy.domain.intent import IntentJournal, IntentStatus, target_position_intent
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderEventKind
from kairospy.application.support.messaging import Message
from kairospy.infrastructure.messaging import InMemoryMessageBus


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
ACCOUNT = AccountContext(AccountBookRef("paper", "account"), Environment.PAPER)


def _risk(limit: str = "100") -> RiskApplication:
    risk = RiskApplication()
    risk.configure((RiskBudget("account-notional", BudgetRef("account", ACCOUNT.book.value), RiskMetric.NOTIONAL, Decimal(limit)),))
    return risk


def _request(request_id: str, amount: str) -> RiskAssessmentRequest:
    return RiskAssessmentRequest(
        request_id,
        (RiskUsage(RiskMetric.NOTIONAL, Decimal(amount), (BudgetRef("account", ACCOUNT.book.value),)),),
        AT,
    )


def test_risk_actor_executes_commands_through_serial_mailbox() -> None:
    risk = _risk()
    actor = RiskActor(risk)

    async def scenario() -> None:
        await actor.start()
        try:
            result = await actor.dispatch_command(AssessRiskCommand(_request("assessment-1", "40")))
            assert result.decision.value == "allowed"
            reservation = await actor.dispatch_command(
                ReserveRiskCommand(RiskReservationRequest("reservation-1", _request("assessment-1", "40")))
            )
            assert reservation.reservation.reservation_id == "reservation-1"
            assert risk.snapshot().budgets[0].reserved == Decimal("40")
        finally:
            await actor.stop()

    asyncio.run(scenario())


def test_risk_reservation_event_preserves_operation_correlation() -> None:
    risk = _risk()
    bus = InMemoryMessageBus()
    actor = RiskActor(risk, bus=bus)

    async def scenario() -> None:
        await actor.start()
        try:
            await actor.dispatch_command(
                ReserveRiskCommand(RiskReservationRequest("reservation-correlation", _request("assessment-correlation", "10"))),
                correlation_id="intent-correlation",
            )
            event = bus.history()[-1]
            assert event.topic == "risk.reservation.updated"
            assert event.correlation_id == "intent-correlation"
            assert event.causation_id is not None
        finally:
            await actor.stop()

    asyncio.run(scenario())


def test_account_actor_asks_risk_actor_before_creating_order() -> None:
    risk = _risk("100")
    risk_actor = RiskActor(risk)
    journal = IntentJournal()
    coordinator = build_execution_coordinator()
    execution = ExecutionApplication.compose(coordinator, intents=journal)

    class Accounts:
        def accounts(self) -> tuple[AccountContext, ...]:
            return (ACCOUNT,)

        def snapshot(self, account: AccountBookRef) -> None:
            return None

    actor = AccountActor(
        None,
        None,
        account_application=Accounts(),
        execution_application=execution,
        intents=journal,
        risk_actor=risk_actor,
    )
    intent = target_position_intent(
        strategy_id="risk-strategy",
        instrument_id="BTCUSDT",
        account_book=ACCOUNT.book.book_key,
        target_quantity=Decimal("2"),
        limit_price=Decimal("60"),
        at=AT,
        intent_id="risk-intent-1",
    )

    async def scenario() -> None:
        await risk_actor.start()
        await actor.start()
        try:
            await actor.dispatch_command(RecordIntentsCommand((intent,), AT))
            result = await actor.dispatch_command(
                ExecuteIntentCommand(intent, SimpleNamespace(now=AT, intents=journal))
            )
            assert result is None
            assert execution.orders() == ()
            assert journal.get(intent.intent_id).status is IntentStatus.REJECTED
            assert risk.snapshot().budgets[0].available == Decimal("100")
        finally:
            await actor.stop()
            await risk_actor.stop()

    asyncio.run(scenario())


def test_account_actor_releases_risk_reservation_on_cancel_update() -> None:
    risk = _risk("100")
    risk_actor = RiskActor(risk)
    journal = IntentJournal()
    coordinator = build_execution_coordinator()
    execution = ExecutionApplication.compose(coordinator, intents=journal)

    class Accounts:
        def accounts(self) -> tuple[AccountContext, ...]:
            return (ACCOUNT,)

        def snapshot(self, account: AccountBookRef) -> None:
            return None

    actor = AccountActor(
        None,
        None,
        account_application=Accounts(),
        execution_application=execution,
        intents=journal,
        risk_actor=risk_actor,
    )
    intent = target_position_intent(
        strategy_id="risk-release-strategy",
        instrument_id="BTCUSDT",
        account_book=ACCOUNT.book.book_key,
        target_quantity=Decimal("1"),
        limit_price=Decimal("40"),
        at=AT,
        intent_id="risk-release-intent",
    )

    async def scenario() -> None:
        await risk_actor.start()
        await actor.start()
        try:
            await actor.dispatch_command(RecordIntentsCommand((intent,), AT))
            order = await actor.dispatch_command(ExecuteIntentCommand(intent, SimpleNamespace(now=AT, intents=journal)))
            assert order is not None
            assert risk.snapshot().budgets[0].reserved == Decimal("40")
            await actor.handle(
                Message(
                    "execution.update",
                    ExecutionUpdate(AT, OrderEventKind.ACKNOWLEDGED, order_id=order.order_id, order_venue_id="venue-1"),
                    AT,
                    "execution",
                    1,
                )
            )
            await actor.handle(
                Message(
                    "execution.update",
                    ExecutionUpdate(AT, OrderEventKind.CANCELED, order_id=order.order_id),
                    AT,
                    "execution",
                    1,
                )
            )
            assert risk.snapshot().budgets[0].reserved == Decimal("0")
            assert risk.snapshot().budgets[0].used == Decimal("0")
        finally:
            await actor.stop()
            await risk_actor.stop()

    asyncio.run(scenario())
