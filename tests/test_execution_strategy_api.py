from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from kairospy.investment.apps.execution.application import (
    ArbitrageLegRequest,
    ExecutionAccountNotEnabledError,
    ExecutionApplication,
    ExecutionIntent,
    ImmediateAlgorithm,
    IntentNotFoundError,
    IntentStatus,
    OrderSide,
    PairArbitrageRequest,
    QuoteRefreshRequest,
    SubmissionStatus,
)
from kairospy.investment.apps.execution.application.events import (
    ExecutionChangeRecord,
    ExecutionEventRecord,
)
from kairospy.investment.apps.execution.services import ExecutionEventCursorCheckpoint
from kairospy.primitives.account import AccountId
from kairospy.primitives.execution import IntentId, OrderId
from kairospy.primitives.reference import InstrumentId
from kairospy.strategy import CommandResult


class RecordingExecutionCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, str]]] = []

    def target_position(self, request, **identity):
        self.calls.append(("target_position", request, identity))
        return CommandResult(
            identity["request_id"], "accepted", {"intent_id": "intent-target"}
        )

    def submit_order(self, request, **identity):
        self.calls.append(("submit_order", request, identity))
        return CommandResult(
            identity["request_id"], "accepted", {"order_id": "order-1"}
        )

    def pair_arbitrage(self, request, **identity):
        self.calls.append(("pair_arbitrage", request, identity))
        return CommandResult(
            identity["request_id"], "accepted", {"intent_id": "intent-pair"}
        )

    def refresh_quote(self, request, **identity):
        self.calls.append(("refresh_quote", request, identity))
        return CommandResult(
            identity["request_id"], "accepted", {"intent_id": request.intent_id}
        )


class CurrentViews:
    def __init__(self, intents=()) -> None:
        self.intents = {str(value.id): value for value in intents}

    def get_intent(self, intent_id: str):
        return self.intents.get(intent_id)

    def get_order(self, order_id: str):
        return None

    def open_orders(self, *, account_id: str | None = None):
        return ()


def application(commands=None, current_views=None) -> ExecutionApplication:
    return ExecutionApplication(
        commands,
        current_views,
        strategy_id="strategy-a",
        instance_id="instance-1",
        account_ids=(AccountId("main"), AccountId("hedge")),
    )


def test_for_account_is_only_single_account_sugar_over_canonical_requests() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, CurrentViews())
    main = execution.for_account("main", segment="usd_m_futures")

    target = main.target_position(
        InstrumentId("instrument:test:BTC-PERP"),
        Decimal("2"),
        algorithm=ImmediateAlgorithm(),
    )
    order = main.limit_order(
        InstrumentId("instrument:test:BTC-PERP"),
        Decimal("1"),
        Decimal("65000"),
        side=OrderSide.BUY,
        post_only=True,
    )

    assert target.accepted and order.accepted
    target_request = commands.calls[0][1]
    order_request = commands.calls[1][1]
    assert target_request.account_id == "main"
    assert target_request.segment_key == "usd_m_futures"
    assert str(order_request.account) == "main"
    assert str(order_request.segment) == "usd_m_futures"
    assert order_request.post_only is True


def test_root_execute_submits_one_cross_account_intent() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, CurrentViews())
    request = PairArbitrageRequest(
        ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
        ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), "hedge"),
        algorithm=ImmediateAlgorithm(),
    )

    receipt = execution.execute(request)

    assert receipt.accepted
    assert receipt.intent_id == IntentId("intent-pair")
    assert commands.calls[0][0] == "pair_arbitrage"
    assert commands.calls[0][1] is request


def test_cross_account_intent_rejects_entire_request_before_transport() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, CurrentViews())
    request = PairArbitrageRequest(
        ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
        ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), "outside"),
        algorithm=ImmediateAlgorithm(),
    )

    receipt = execution.execute(request)

    assert receipt.status is SubmissionStatus.REJECTED
    assert not receipt.may_have_been_sent
    assert "outside" in (receipt.error or "")
    assert commands.calls == []


def test_for_account_rejects_unconfigured_account_at_selection_time() -> None:
    with pytest.raises(ExecutionAccountNotEnabledError, match="outside"):
        application(RecordingExecutionCommands(), CurrentViews()).for_account("outside")


def test_intent_queries_filter_strategy_and_complete_account_scope() -> None:
    instrument = InstrumentId("instrument:test:BTCUSDT")
    owned = ExecutionIntent(
        IntentId("owned"),
        "strategy-a",
        _instrument(instrument),
        (AccountId("main"), AccountId("hedge")),
        Decimal("1"),
        IntentStatus.ACTIVE,
        "",
        (),
    )
    foreign_strategy = ExecutionIntent(
        IntentId("foreign-strategy"),
        "strategy-b",
        _instrument(instrument),
        (AccountId("main"),),
        Decimal("1"),
        IntentStatus.ACTIVE,
        "",
        (),
    )
    foreign_account = ExecutionIntent(
        IntentId("foreign-account"),
        "strategy-a",
        _instrument(instrument),
        (AccountId("main"), AccountId("outside")),
        Decimal("1"),
        IntentStatus.ACTIVE,
        "",
        (),
    )
    execution = application(
        RecordingExecutionCommands(),
        CurrentViews((owned, foreign_strategy, foreign_account)),
    )

    assert execution.require_intent(IntentId("owned")) is owned
    assert execution.intent(IntentId("foreign-strategy")) is None
    assert execution.intent(IntentId("foreign-account")) is None
    with pytest.raises(IntentNotFoundError):
        execution.require_intent(IntentId("missing"))


def test_refresh_quote_requires_an_owned_intent() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, CurrentViews())
    request = QuoteRefreshRequest("outside-intent", Decimal("99"), Decimal("101"), 123)

    receipt = execution.refresh_quote(request)

    assert not receipt.accepted
    assert not receipt.may_have_been_sent
    assert commands.calls == []


def test_rejected_receipt_require_accepted_preserves_delivery_semantics() -> None:
    receipt = application(None, None).target_position(
        InstrumentId("instrument:test:BTCUSDT"),
        Decimal("1"),
        account="main",
        algorithm=ImmediateAlgorithm(),
    )

    assert not receipt.accepted
    assert not receipt.may_have_been_sent
    with pytest.raises(RuntimeError, match="disabled"):
        receipt.require_accepted()


class EventSource:
    def __init__(self, records: tuple[ExecutionEventRecord, ...]) -> None:
        self.records = records

    async def subscribe_live(self):
        for record in self.records:
            yield record

    def check_ready(self) -> None:
        return None


def _event_record(sequence: int) -> ExecutionEventRecord:
    return ExecutionEventRecord(
        "execution.events",
        sequence,
        "execution",
        "instance-1",
        (),
        sequence,
    )


def test_execution_event_cursor_ignores_duplicates_and_reports_gaps() -> None:
    execution = ExecutionApplication(
        None,
        None,
        EventSource((_event_record(1), _event_record(1), _event_record(2))),
        strategy_id="strategy-a",
        instance_id="instance-1",
    )
    asyncio.run(_drain(execution))
    assert execution.health() == {
        "event_source_ready": False,
        "event_cursor": 2,
        "processing_event_cursor": 2,
        "event_lag": 0,
        "event_gap_count": 0,
        "event_scope_error_count": 0,
        "event_recovery_count": 0,
        "event_recovery_incomplete": False,
    }

    gap = ExecutionApplication(
        None,
        None,
        EventSource((_event_record(4), _event_record(6))),
        strategy_id="strategy-a",
        instance_id="instance-1",
    )
    with pytest.raises(RuntimeError, match="not contiguous"):
        asyncio.run(_drain(gap))
    assert gap.health()["event_gap_count"] == 1


def test_execution_cursor_checkpoints_only_after_record_consumption(
    tmp_path,
) -> None:
    checkpoint = ExecutionEventCursorCheckpoint(
        tmp_path / "cursor.json", instance_id="instance-1"
    )
    change = ExecutionChangeRecord(
        "intent_update",
        "strategy-a",
        "main",
        {
            "intent": {
                "intent_id": "intent-1",
                "instrument_id": "instrument:test:BTCUSDT",
                "account_ids": ["main"],
                "strategy_decision_id": "decision-1",
            },
            "status": "accepted",
            "previous_status": None,
            "order_ids": [],
            "reason": "",
        },
    )
    execution = ExecutionApplication(
        None,
        None,
        EventSource(
            (
                ExecutionEventRecord(
                    "execution.events",
                    1,
                    "execution",
                    "instance-1",
                    (change,),
                    1,
                ),
            )
        ),
        strategy_id="strategy-a",
        instance_id="instance-1",
        account_ids=(AccountId("main"),),
        cursor_checkpoint=checkpoint,
    )

    async def consume() -> None:
        iterator = execution.events()
        await anext(iterator)
        assert checkpoint.load() == 0
        assert execution.health()["event_cursor"] == 0
        assert execution.health()["processing_event_cursor"] == 1
        assert execution.health()["event_lag"] == 1
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(consume())
    assert checkpoint.load() == 1
    restored = ExecutionApplication(
        None,
        None,
        EventSource(()),
        strategy_id="strategy-a",
        instance_id="instance-1",
        cursor_checkpoint=checkpoint,
    )
    assert restored.health()["event_cursor"] == 1


def test_execution_cursor_recovers_decision_progress_from_current_view(
    tmp_path,
) -> None:
    intent = ExecutionIntent(
        IntentId("intent-recovered"),
        "strategy-a",
        _instrument(InstrumentId("instrument:test:BTCUSDT")),
        (AccountId("main"),),
        Decimal("1"),
        IntentStatus.SATISFIED,
        "filled while Strategy was stopped",
        (OrderId("order-1"),),
        strategy_decision_id="decision-recovered",
        updated_at_unix_nanos=10,
    )

    class CurrentView:
        def recovery_snapshot(self):
            return 7, (intent,), (), True

    class Decisions:
        def __init__(self) -> None:
            self.values = []

        def reconcile_execution_snapshot(self, value, *, source_event_sequence):
            self.values.append((value, source_event_sequence))

    checkpoint = ExecutionEventCursorCheckpoint(
        tmp_path / "cursor.json", instance_id="instance-1"
    )
    decisions = Decisions()
    execution = ExecutionApplication(
        None,
        CurrentView(),
        EventSource(()),
        strategy_id="strategy-a",
        instance_id="instance-1",
        account_ids=(AccountId("main"),),
        cursor_checkpoint=checkpoint,
    )
    execution.bind_decisions(decisions)
    execution.check_event_source_ready()

    assert decisions.values == [(intent, 7)]
    assert checkpoint.load() == 7
    assert execution.health()["event_recovery_count"] == 1
    assert execution.health()["event_recovery_incomplete"] is True


async def _drain(execution: ExecutionApplication) -> None:
    async for _ in execution.events():
        pass


def _instrument(instrument_id: InstrumentId):
    from kairospy.investment.apps.reference.application import InstrumentRef

    return InstrumentRef(instrument_id, "BTCUSDT")
