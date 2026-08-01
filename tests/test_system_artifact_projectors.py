from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.runtime.orchestration.pipeline import RuntimeProjectionPipeline
from kairospy.application.runtime.processors.system import runtime_processors
from kairospy.application.runtime.services import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.system.projectors import TimelineProjector
from kairospy.core.account import AccountContext, AccountBookRef, Environment
from kairospy.core.execution import ExecutionCoordinator, FillReport, cash_order_request
from kairospy.core.intent import IntentJournal
from kairospy.core.order import OrderSide
from kairospy.core.views import ViewStore


def test_timeline_projector_order_triggers_follow_execution_state_deltas() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = AccountContext(AccountBookRef("paper", "main"), Environment.PAPER)
    execution = ExecutionCoordinator()
    intents = IntentJournal()
    services = RuntimeApplicationServices.from_dependencies(
        RuntimeServiceDependencies(intents=intents, execution_coordinator=execution)
    )
    output = MemoryTimelineOutput()
    projector = TimelineProjector(output, sample_interval="off")  # type: ignore[arg-type]
    views = ViewStore()
    pipeline = RuntimeProjectionPipeline(
        views=views,
        processors=runtime_processors(
            strategy_id="s",
            intents=intents,
            services=services,
        ),
    )

    execution.plan_order(
        cash_order_request(
            order_id="order-1",
            context=context,
            instrument_id="instrument:spot:btc:usdt",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        ),
        at=now,
    )
    pipeline.publish()
    projector.publish_views(views, as_of=now)
    execution.submit_order("order-1", at=now)
    pipeline.publish()
    projector.publish_views(views, as_of=now)
    execution.ingest_fill(
        FillReport(
            "order-1",
            now,
            Decimal("1"),
            Decimal("100"),
            "USDT",
            Decimal("-100"),
        )
    )
    pipeline.publish()
    projector.publish_views(views, as_of=now)

    assert [row["trigger"] for row in output.rows] == ["order_created", "order_submitted", "fill"]


class MemoryTimelineOutput:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append_history(self, stream: str, record: object) -> None:
        assert stream == "timeline"
        self.rows.append(dict(record))  # type: ignore[arg-type]
