from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.query import AccountQueryService
from kairospy.core.account import (
    AccountBalance,
    AccountContext,
    AccountCurrentView,
    AccountBookRef,
    AccountSource,
    Environment,
    PositionSnapshot,
    account_current_schema,
)
from kairospy.core.views import ViewStore


def test_account_query_service_reads_current_balances_positions_and_orders() -> None:
    views = _account_views()
    service = AccountQueryService(views)

    current = service.current()

    assert current.cash == Decimal("1000")
    assert service.balances()[0].currency == "USDT"
    assert service.balance("USDT").total == Decimal("1000")
    assert service.positions()[0].quantity == Decimal("0.01")
    assert service.position("market:binance:spot:btc_usdt").quantity == Decimal("0.01")
    assert service.open_orders()[0].order_id == "venue-order-1"
    assert service.pending_orders()[0].order_id == "local-order-1"


def test_runtime_context_accounts_uses_shared_account_query_service() -> None:
    context = RuntimeContext("strategy", views=_account_views())

    assert isinstance(context.accounts, AccountQueryService)
    assert context.account().cash == Decimal("1000")
    assert context.accounts.balance("USDT").free == Decimal("1000")


def _account_views() -> ViewStore:
    views = ViewStore()
    account = AccountContext(AccountBookRef("binance", "main", "spot"), Environment.PAPER)
    key = "account.current.paper.binance.main.spot"
    views.register(account_current_schema(key))
    views.put_runtime(
        key,
        AccountCurrentView(
            context=account,
            book=account.book,
            balances=(AccountBalance.from_total_locked("USDT", Decimal("1000"), Decimal("0"), source=AccountSource.LEDGER),),
            positions=(PositionSnapshot("market:binance:spot:btc_usdt", Decimal("0.01"), AccountSource.LEDGER),),
            open_orders=(SimpleNamespace(order_id="venue-order-1"),),
            pending_orders=(SimpleNamespace(order_id="local-order-1"),),
            cash=Decimal("1000"),
            equity=Decimal("1000"),
        ),
    )
    return views
