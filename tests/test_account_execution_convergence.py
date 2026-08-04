from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.account.application.accounts import AccountApplicationService
from kairospy.application.usecases.account.protocol import AccountLoginRequest, AccountLoginResult, AccountSession
from kairospy.application.usecases.execution.application.component import ExecutionApplication, ExecuteIntentCommand
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
from kairospy.application.usecases.execution.domain.policy import ExecutionSafetyPolicy
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderCancelResult, ConnectionOrderSubmissionRequest, ConnectionOrderSubmissionResult
from kairospy.domain.account import AccountBookRef, AccountContext, Environment
from kairospy.domain.intent import IntentJournal, IntentStatus, target_position_intent


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class LoginPort:
    def login(self, request: AccountLoginRequest) -> AccountLoginResult:
        return AccountLoginResult(AccountSession("session-1", request.context.book, logged_in_at=request.observed_at))

    def logout(self, session: AccountSession) -> None:
        return None


def test_account_login_is_a_session_usecase() -> None:
    context = AccountContext(AccountBookRef("binance", "acct"), Environment.LIVE)
    account = AccountApplicationService((context,), login_port=LoginPort())

    result = account.login(context.book, credential_ref="credential-1", at=AT)

    assert result.session.session_id == "session-1"
    assert result.session.account == context.book


class OrderEntry:
    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        assert request.symbol == "BTCUSDT"
        return ConnectionOrderSubmissionResult("venue-1", "NEW")

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        return ConnectionOrderCancelResult(request.order_venue_id, "CANCELED")


@dataclass
class ExecutionContext:
    now: datetime
    intents: IntentJournal


def test_execution_converts_intent_to_order_and_delegates_submit() -> None:
    context = AccountContext(AccountBookRef("binance", "acct"), Environment.LIVE)
    intent = target_position_intent(
        strategy_id="strategy-1",
        instrument_id="BTCUSDT",
        account_book="spot",
        target_quantity=Decimal("1"),
        at=AT,
        intent_id="intent-1",
    )
    journal = IntentJournal()
    journal.record_intent(intent, at=AT)
    execution = ExecutionApplication.compose(
        build_execution_coordinator(),
        order_connection=OrderEntry(),
        symbol_resolver=lambda instrument: "BTCUSDT",
        intents=journal,
    )

    order = execution.execute_intent(
        ExecuteIntentCommand(
            intent=intent,
            context=ExecutionContext(AT, journal),
            account=context,
            current_quantity=Decimal("0"),
            safety_policy=ExecutionSafetyPolicy(trading_enabled=True, require_limit_orders=False),
        )
    )

    assert order is not None
    assert order.order_venue_id == "venue-1"
    assert journal.get("intent-1").status is IntentStatus.ORDERING
