from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.execution.application.component import ExecutionApplication
from kairospy.application.usecases.execution.application.component import (
    PlanOrderCommand,
    SubmitOrderCommand,
)
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
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
