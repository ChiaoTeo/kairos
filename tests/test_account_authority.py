from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairospy.application.actor.account.application import (
    AccountActor,
    QueryAccountCommand,
    RefreshAccountCommand,
)
from kairospy.application.usecases.account.application.accounts import AccountApplicationService
from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, Environment, ProductFamily
from kairospy.application.usecases.account.application.read import (
    AccountQueryRequest,
    AccountReadMode,
    AccountRefreshRequest,
)
from kairospy.application.usecases.account.protocol import AccountReadRequest
from kairospy.application.usecases.account.application.projectors import _portfolio_views
from kairospy.domain.account import AccountBalance, AccountSegment, AccountRuntimeContext, AccountSnapshot, AccountSource, Environment
from kairospy.domain.account.views import AccountCurrentView
from kairospy.application.support.messaging import Message


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONTEXT = AccountRuntimeContext(AccountSegment("test", "main", AccountModel.NO_MARGIN, ProductFamily.SPOT), Environment.LIVE)


class Reader:
    def read_account(self, request: AccountReadRequest) -> AccountSnapshot:
        return AccountSnapshot(
            request.context,
            balances=(AccountBalance.from_free_locked("USD", Decimal("7"), Decimal("3"), source=AccountSource.VENUE),),
            observed_at=request.observed_at,
        )


def test_account_query_reports_age_and_staleness_from_request_policy() -> None:
    app = AccountApplicationService(CONTEXT, account_reader=Reader())
    app.refresh(AccountRefreshRequest(CONTEXT.segment, at=NOW - timedelta(seconds=10)))

    result = app.query(AccountQueryRequest(CONTEXT.segment, max_age_seconds=5, now=NOW))

    assert result.mode is AccountReadMode.CACHED
    assert result.age_seconds == 10
    assert result.stale is True
    assert result.account_state is not None
    assert result.account_state.source is AccountSource.STALE


def test_account_actor_routes_query_and_refresh_commands_to_account_application() -> None:
    app = AccountApplicationService(CONTEXT, account_reader=Reader())
    actor = AccountActor(None, None, account_application=app)

    refreshed = actor.apply_command(RefreshAccountCommand(AccountRefreshRequest(CONTEXT.segment, at=NOW)))
    queried = actor.apply_command(QueryAccountCommand(AccountQueryRequest(CONTEXT.segment, now=NOW)))

    assert refreshed.read.snapshot.observed_at == NOW
    assert queried.snapshot == refreshed.read.snapshot


def test_portfolio_does_not_sum_books_with_unknown_valuation_currency() -> None:
    first = AccountCurrentView(CONTEXT, segment=CONTEXT.segment, balances=(AccountBalance.from_free_locked("USD", Decimal("10"), Decimal("0"), source=AccountSource.VENUE),), selected_balance=Decimal("10"), valuation_asset="USD")
    second_context = AccountRuntimeContext(AccountSegment("test", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.LIVE)
    second = AccountCurrentView(second_context, segment=second_context.segment, balances=(AccountBalance.from_free_locked("EUR", Decimal("10"), Decimal("0"), source=AccountSource.VENUE),), selected_balance=Decimal("10"), valuation_asset="EUR")

    portfolio = _portfolio_views([first, second])[0]

    assert portfolio.selected_balance is None
    assert portfolio.equity is None
    assert portfolio.valuation_asset is None
    assert portfolio.aggregate_complete is False


def test_refresh_command_publishes_the_updated_snapshot_after_state_update() -> None:
    class Bus:
        def __init__(self) -> None:
            self.messages = []

        async def publish(self, message: Message) -> None:
            self.messages.append(message)

    bus = Bus()
    app = AccountApplicationService(CONTEXT, account_reader=Reader())
    actor = AccountActor(None, bus, account_application=app)

    import asyncio

    result = asyncio.run(
        actor.process(
            Message(
                "account.command",
                RefreshAccountCommand(AccountRefreshRequest(CONTEXT.segment, at=NOW)),
                NOW,
                "test",
                1,
                message_id="refresh-command",
            )
        )
    )

    assert result.read.snapshot == app.snapshot(CONTEXT.segment)
    assert len(bus.messages) == 1
    assert bus.messages[0].topic == "account.snapshot"
    assert bus.messages[0].causation_id == "refresh-command"
