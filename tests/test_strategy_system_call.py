from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.support.runtime.application.interaction import SystemCallDecision, SystemCallResult
from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand, RuntimeCommandStatus
from kairospy.application.support.messaging import Message
from message_helpers import message as make_message
from kairospy.application.usecases.strategy.application.runtime import build_strategy_runtime_session
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.domain.market.selection import MarketSelection, MarketSelectionQuery
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef


class RecordingSystemCall:
    def __init__(self) -> None:
        self.commands: list[RuntimeCommand] = []
        self.intents: list[object] = []

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        self.commands.append(command)
        handle = CommandHandle(command.command_id, command.kind)
        handle._accept({"accepted_by": "system"})
        return SystemCallResult(
            request_id=command.command_id,
            decision=SystemCallDecision.ACCEPTED,
            handle=handle,
            result=handle.result,
        )

    def emit_intent(self, intent: object, *, context: object | None = None) -> None:
        _ = context
        self.intents.append(intent)


def test_strategy_context_reenters_system_during_strategy_callback() -> None:
    calls: list[str] = []
    system_call = RecordingSystemCall()

    class Strategy:
        strategy_id = "system-call-strategy"

        def on_start(self, context: StrategyContext) -> None:
            return None

        def on_data(self, context: StrategyContext, signal: object) -> None:
            calls.append("before")
            handle = context.submit(RuntimeCommand("test.system-call"))
            assert handle.status is RuntimeCommandStatus.ACCEPTED
            calls.append("after")

        def on_end(self, context: StrategyContext) -> None:
            return None

    runtime = build_strategy_runtime_session(Strategy(), system_call=system_call)
    runtime.process(
        make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test"),
        hook="on_data",
    )

    assert calls == ["before", "after"]
    assert [command.kind for command in system_call.commands] == ["test.system-call"]
    assert runtime.frame.event_count == 1
    assert [callback.hook for callback in runtime.frame.callbacks] == ["on_start", "on_data"]


def test_runtime_constructs_strategy_context_without_runtime_context_wrapper() -> None:
    class Strategy:
        strategy_id = "direct-context-strategy"

        def on_start(self, context: StrategyContext) -> None:
            assert not hasattr(context, "_runtime")

        def on_data(self, context: StrategyContext, signal: object) -> None:
            assert not hasattr(context, "_runtime")

        def on_end(self, context: StrategyContext) -> None:
            assert not hasattr(context, "_runtime")

    runtime = build_strategy_runtime_session(Strategy())
    runtime.process(
        make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test"),
        hook="on_data",
    )


def test_strategy_intent_can_reenter_system_while_callback_is_active() -> None:
    system_call = RecordingSystemCall()
    marker = type("Intent", (), {"strategy_id": "intent-system-call-strategy"})()

    class Strategy:
        strategy_id = "intent-system-call-strategy"

        def on_start(self, context: StrategyContext) -> None:
            return None

        def on_data(self, context: StrategyContext, signal: object) -> None:
            context.intent(marker)  # type: ignore[arg-type]
            assert system_call.intents == [marker]

        def on_end(self, context: StrategyContext) -> None:
            return None

    runtime = build_strategy_runtime_session(Strategy(), system_call=system_call)
    runtime.process(
        make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test"),
        hook="on_data",
    )

    assert system_call.intents == [marker]


def test_strategy_can_pass_reference_selection_directly_to_subscribe() -> None:
    system_call = RecordingSystemCall()
    context = StrategyContext("selection-strategy", system_call=system_call)
    selection = MarketSelection(
        markets=(
            MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT"),
            MarketRef.ephemeral(venue="binance", market="spot", source_symbol="ETH/USDT"),
        ),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        query=MarketSelectionQuery(venue="binance", market="spot"),
    )

    result = context.subscribe(selection, selectors=(Quote,), identity="selection-strategy")

    assert result.accepted
    assert system_call.commands[0].kind == "market.subscribe.batch"
    assert len(system_call.commands[0].payload.requests) == 2
