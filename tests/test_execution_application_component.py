from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.execution.application.component import ExecutionApplication
from kairospy.application.usecases.execution.application.component import (
    PlanOrderCommand,
    SubmitOrderCommand,
)
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
from kairospy.application.usecases.risk.application.budget import RiskApplication
from kairospy.application.usecases.risk.domain import BudgetRef, RiskBudget, RiskMetric
from kairospy.domain.account import AccountSnapshot, AccountSource, MarginScope, MarginState
from kairospy.domain.account import AccountBookRef, AccountContext, Environment
from kairospy.domain.order import OrderRequest, OrderSide, OrderStatus, OrderType


def test_execution_application_is_the_business_entrypoint() -> None:
    application = ExecutionApplication.compose(build_execution_coordinator())
    request = OrderRequest(
        "order-1",
        AccountContext(AccountBookRef("broker", "account"), Environment.PAPER),
        "instrument:BTC:USD",
        OrderSide.BUY,
        Decimal("1"),
        OrderType.MARKET,
    )
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    planned = application.plan_order(PlanOrderCommand(request=request, at=at))
    submitted = application.submit_order(SubmitOrderCommand("order-1", at))

    assert planned.order_id == "order-1"
    assert submitted.status is OrderStatus.SUBMITTING
    assert application.current_view().total_orders == 1


def test_default_execution_composition_installs_risk_application() -> None:
    coordinator = build_execution_coordinator()

    assert coordinator.risk is not None


def test_execution_reserves_risk_budget_before_margin_reservation() -> None:
    context = AccountContext(AccountBookRef("broker", "account"), Environment.PAPER)
    risk = RiskApplication()
    risk.configure(
        (
            RiskBudget(
                "account-margin",
                BudgetRef("account", context.book.value),
                RiskMetric.MARGIN,
                Decimal("50"),
            ),
        )
    )
    application = ExecutionApplication.compose(build_execution_coordinator(risk=risk))
    request = OrderRequest(
        "order-risk-1",
        context,
        "instrument:BTC:USD",
        OrderSide.BUY,
        Decimal("1"),
        OrderType.MARKET,
    )
    snapshot = AccountSnapshot(
        context,
        balances=(),
        margins=(
            MarginState(
                "USD",
                Decimal("100"),
                Decimal("50"),
                AccountSource.VENUE,
                scope=MarginScope.ACCOUNT,
                available=Decimal("100"),
            ),
        ),
    )

    planned = application.plan_order(
        PlanOrderCommand(
            request=request,
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reserve_currency="USD",
            margin_notional=Decimal("100"),
            margin_leverage=Decimal("2"),
            venue_snapshot=snapshot,
        )
    )

    assert planned.status is OrderStatus.RESERVED
    budget = risk.snapshot().budgets[0]
    assert budget.reserved == Decimal("50")


def test_limit_order_can_use_risk_budget_without_cash_parameters() -> None:
    context = AccountContext(AccountBookRef("broker", "account"), Environment.PAPER)
    risk = RiskApplication()
    risk.configure(
        (
            RiskBudget(
                "account-notional",
                BudgetRef("account", context.book.value),
                RiskMetric.NOTIONAL,
                Decimal("100"),
            ),
        )
    )
    application = ExecutionApplication.compose(build_execution_coordinator(risk=risk))
    request = OrderRequest(
        "order-risk-2",
        context,
        "instrument:BTC:USD",
        OrderSide.BUY,
        Decimal("2"),
        OrderType.LIMIT,
        limit_price=Decimal("40"),
    )

    planned = application.plan_order(
        PlanOrderCommand(request=request, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    )

    assert planned.status is OrderStatus.RESERVED
    assert risk.snapshot().budgets[0].reserved == Decimal("80")
