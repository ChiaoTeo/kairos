from __future__ import annotations

from decimal import Decimal

import pytest

from kairospy.application.execution import (
    ArbitrageLegRequest,
    ExecutionAccountNotEnabledError,
    ExecutionApplication,
    ExecutionIntent,
    IntentNotFoundError,
    IntentStatus,
    OrderSide,
    PairArbitrageRequest,
    QuoteRefreshRequest,
    SubmissionStatus,
)
from kairospy.domain_types import AccountId, InstrumentId, IntentId, OrderId
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


class Projection:
    def __init__(self, intents=()) -> None:
        self.intents = {str(value.id): value for value in intents}

    def get_intent(self, intent_id: str):
        return self.intents.get(intent_id)

    def get_order(self, order_id: str):
        return None

    def open_orders(self, *, account_id: str | None = None):
        return ()


def application(commands=None, projection=None) -> ExecutionApplication:
    return ExecutionApplication(
        commands,
        projection,
        strategy_id="strategy-a",
        instance_id="instance-1",
        account_ids=(AccountId("main"), AccountId("hedge")),
    )


def test_for_account_is_only_single_account_sugar_over_canonical_requests() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, Projection())
    main = execution.for_account("main", segment="usd_m_futures")

    target = main.target_position(
        InstrumentId("instrument:test:BTC-PERP"), Decimal("2")
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
    execution = application(commands, Projection())
    request = PairArbitrageRequest(
        ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
        ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), "hedge"),
    )

    receipt = execution.execute(request)

    assert receipt.accepted
    assert receipt.intent_id == IntentId("intent-pair")
    assert commands.calls[0][0] == "pair_arbitrage"
    assert commands.calls[0][1] is request


def test_cross_account_intent_rejects_entire_request_before_transport() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, Projection())
    request = PairArbitrageRequest(
        ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
        ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), "outside"),
    )

    receipt = execution.execute(request)

    assert receipt.status is SubmissionStatus.REJECTED
    assert not receipt.may_have_been_sent
    assert "outside" in (receipt.error or "")
    assert commands.calls == []


def test_for_account_rejects_unconfigured_account_at_selection_time() -> None:
    with pytest.raises(ExecutionAccountNotEnabledError, match="outside"):
        application(RecordingExecutionCommands(), Projection()).for_account("outside")


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
        Projection((owned, foreign_strategy, foreign_account)),
    )

    assert execution.require_intent(IntentId("owned")) is owned
    assert execution.intent(IntentId("foreign-strategy")) is None
    assert execution.intent(IntentId("foreign-account")) is None
    with pytest.raises(IntentNotFoundError):
        execution.require_intent(IntentId("missing"))


def test_refresh_quote_requires_an_owned_intent() -> None:
    commands = RecordingExecutionCommands()
    execution = application(commands, Projection())
    request = QuoteRefreshRequest("outside-intent", Decimal("99"), Decimal("101"), 123)

    receipt = execution.refresh_quote(request)

    assert not receipt.accepted
    assert not receipt.may_have_been_sent
    assert commands.calls == []


def test_rejected_receipt_require_accepted_preserves_delivery_semantics() -> None:
    receipt = application(None, None).target_position(
        InstrumentId("instrument:test:BTCUSDT"), Decimal("1"), account="main"
    )

    assert not receipt.accepted
    assert not receipt.may_have_been_sent
    with pytest.raises(RuntimeError, match="disabled"):
        receipt.require_accepted()


def _instrument(instrument_id: InstrumentId):
    from kairospy.application.reference import InstrumentRef

    return InstrumentRef(instrument_id, "BTCUSDT")
