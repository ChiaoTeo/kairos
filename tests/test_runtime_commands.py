from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kairospy.application.support.runtime.domain.commands import (
    CommandHandle,
    RuntimeCommand,
    RuntimeCommandStatus,
)
from kairospy.application.support.messaging import Message
from message_helpers import message as make_message
from kairospy.application.actor.market.application import MarketActor
from kairospy.application.actor.account.application import AccountActor
from kairospy.application.actor.monitor.application.output import MonitorOutputCoordinator
from kairospy.application.system.application.business import SystemBusinessRuntime
from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.support.runtime.application.interaction import RuntimeInstruction
from kairospy.application.usecases.strategy.application.runtime import build_strategy_runtime_session
from kairospy.application.usecases.strategy.protocol import StrategySubscriptionRequest
from kairospy.application.usecases.market.application.ingestion import MarketIngestionApplicationService
from kairospy.application.usecases.market.application.query import MarketDataQueryApplicationService
from kairospy.application.usecases.market.application.subscriptions import MarketSubscriptionApplicationService
from kairospy.application.usecases.market.domain.subscriptions import MarketSubscriptionService
from kairospy.application.usecases.market.domain.subscriptions import MarketDataSubscriptionGroupSpec, MarketDataSubscriptionSpec
from kairospy.domain.market import Quote
from kairospy.domain.reference import MarketRef
from kairospy.domain.intent import IntentJournal, target_position_intent


def _business_runtime(intents: IntentJournal) -> SystemBusinessRuntime:
    return SystemBusinessRuntime(
        output=MonitorOutputCoordinator(actors=(), monitor_output=object()),
        account_actor=AccountActor(None, None, intents=intents),
    )


def test_runtime_command_has_opaque_payload_and_stable_identity() -> None:
    requested_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = object()
    command = RuntimeCommand("market.subscribe", payload, requested_at=requested_at)

    assert command.kind == "market.subscribe"
    assert command.payload is payload
    assert command.requested_at is requested_at
    assert command.command_id


def test_command_handle_exposes_lifecycle_without_becoming_business_state() -> None:
    handle = CommandHandle("command-1", "market.subscribe")

    assert handle.status is RuntimeCommandStatus.PENDING
    assert not handle.accepted
    assert not handle.done

    handle._accept(at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert handle.status is RuntimeCommandStatus.ACCEPTED
    assert handle.accepted
    assert not handle.done

    handle._complete({"subscription_id": "sub-1"})
    assert handle.status is RuntimeCommandStatus.COMPLETED
    assert handle.done
    assert handle.result["subscription_id"] == "sub-1"

    with pytest.raises(RuntimeError, match="already"):
        handle._cancel()


def test_rejected_handle_is_not_accepted() -> None:
    handle = CommandHandle("command-2", "market.subscribe")

    handle._reject("connection unavailable")

    assert handle.status is RuntimeCommandStatus.REJECTED
    assert not handle.accepted
    assert handle.done
    assert handle.error == "connection unavailable"


def test_command_handle_supports_deferred_and_ignored_terminal_lifecycles() -> None:
    deferred = CommandHandle("command-deferred", "test")
    deferred._defer({"reason": "outside session window"})
    assert deferred.status is RuntimeCommandStatus.DEFERRED
    assert deferred.accepted
    assert not deferred.done
    deferred._complete({"value": "done"})
    assert deferred.done

    ignored = CommandHandle("command-ignored", "test")
    ignored._ignore({"reason": "duplicate"})
    assert ignored.status is RuntimeCommandStatus.IGNORED
    assert ignored.done


def test_runtime_event_can_correlate_a_system_transition_to_a_command() -> None:
    event = Message(
        topic="system.command.completed",
        payload={"status": "completed"},
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        producer="test",
        producer_sequence=1,
        correlation_id="strategy-event-1",
        command_id="command-1",
    )

    assert event.command_id == "command-1"
    assert event.correlation_id == "strategy-event-1"


def test_system_interaction_accepts_market_registration_and_deduplicates_command() -> None:
    class Market:
        def __init__(self) -> None:
            self.subscriptions = MarketSubscriptionService()

        def subscribe(self, spec: MarketDataSubscriptionSpec):
            return self.subscriptions.subscribe(spec)

        def unsubscribe(self, subscription):
            self.subscriptions.unsubscribe(subscription)

    market = Market()
    interaction = MarketActor(None, None, market_service=market)
    command = RuntimeCommand(
        "market.subscribe",
        MarketDataSubscriptionSpec(
            MarketRef.ephemeral(venue="binance", market="spot", source_symbol="AAPL"),
            (Quote,),
        ),
        command_id="command-market-1",
    )

    first = interaction.call(command)
    second = interaction.call(command)

    assert first is second
    assert first.status is RuntimeCommandStatus.ACCEPTED
    assert first.result["subscription_id"]
    assert len(market.subscriptions.subscriptions()) == 1

    interaction.apply_event(
        Message(
            topic="system.subscription.activated",
            payload={"remote_id": "remote-1"},
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            producer="test",
            producer_sequence=1,
            command_id=command.command_id,
        )
    )

    assert first.status is RuntimeCommandStatus.COMPLETED
    assert first.handle is not None
    assert first.handle.result["remote_id"] == "remote-1"


def test_system_interaction_accepts_a_batch_subscription_selection() -> None:
    market = MarketApplication()
    interaction = MarketActor(None, None, market_service=market)
    specs = MarketDataSubscriptionGroupSpec(
        (
            MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT"), (Quote,), identity="strategy"),
            MarketDataSubscriptionSpec(MarketRef.ephemeral(venue="binance", market="spot", source_symbol="ETH/USDT"), (Quote,), identity="strategy"),
        )
    )

    result = interaction.call(RuntimeCommand("market.subscribe.batch", specs))

    assert result.accepted
    assert len(result.result["subscription_ids"]) == 2
    assert len(market.subscriptions.subscriptions()) == 2


def test_system_translates_strategy_subscription_intent_inside_biz() -> None:
    market = MarketApplication()
    interaction = MarketActor(None, None, market_service=market)
    request = StrategySubscriptionRequest(
        subject="BTC/USDT",
        selectors=(Quote,),
        exchange="binance",
        market_type="spot",
        identity="strategy",
    )

    result = interaction.call(RuntimeCommand("market.subscribe", request))

    assert result.accepted
    subscriptions = market.subscriptions.subscriptions()
    assert len(subscriptions) == 1
    assert subscriptions[0].spec.market.source_symbol == "BTC/USDT"


def test_system_call_is_the_primary_command_entrypoint() -> None:
    class Market:
        def __init__(self) -> None:
            self.subscriptions = MarketSubscriptionService()

    service = MarketActor(None, None, market_service=Market())
    handle = service.call(RuntimeCommand("unsupported.command"))

    assert handle.status is RuntimeCommandStatus.REJECTED


def test_system_policy_can_defer_or_ignore_a_command() -> None:
    def policy(command: RuntimeCommand) -> RuntimeCommandStatus | None:
        return {
            "defer.command": RuntimeCommandStatus.DEFERRED,
            "ignore.command": RuntimeCommandStatus.IGNORED,
        }.get(command.kind)

    service = MarketActor(None, None)
    service.policy = policy

    deferred = service.call(RuntimeCommand("defer.command"))
    ignored = service.call(RuntimeCommand("ignore.command"))

    assert deferred.status is RuntimeCommandStatus.DEFERRED
    assert ignored.status is RuntimeCommandStatus.IGNORED


def test_market_service_exposes_capabilities_without_flattening_them() -> None:
    market = MarketApplication()

    assert isinstance(market.subscriptions, MarketSubscriptionApplicationService)
    assert isinstance(market.queries, MarketDataQueryApplicationService)
    assert isinstance(market.ingestion, MarketIngestionApplicationService)
    assert not hasattr(market, "subscribe")
    assert not hasattr(market, "download")

    subscription = market.subscriptions.subscribe(
        MarketDataSubscriptionSpec(
            MarketRef.ephemeral(venue="binance", market="spot", source_symbol="AAPL"),
            (Quote,),
        )
    )
    assert market.subscriptions.subscriptions() == (subscription,)


def test_runtime_routes_each_message_through_system_callback_before_strategy() -> None:
    order: list[str] = []

    class Callback:
        def on_message(self, event):
            order.append("system.message")
            return RuntimeInstruction(strategy_hook="on_data")

        def on_strategy_result(self, event, *, hook, intents, context):
            order.append("system.result")
            return RuntimeInstruction()

    class Strategy:
        strategy_id = "callback-strategy"

        def on_start(self, context) -> None:
            return None

        def on_data(self, context, event) -> None:
            order.append("strategy")

        def on_end(self, context) -> None:
            return None

    event = make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test")
    callback = Callback()
    session = build_strategy_runtime_session(Strategy())
    instruction = callback.on_message(event)
    session.process(event, hook=instruction.strategy_hook)
    callback.on_strategy_result(event, hook="on_data", intents=(), context=SimpleNamespace(now=event.time))

    assert order == ["system.message", "strategy", "system.result"]


def test_runtime_system_callback_can_hold_or_stop_before_strategy() -> None:
    dispatched: list[int] = []

    class Callback:
        def on_message(self, event):
            return RuntimeInstruction("hold" if event.sequence == 1 else "stop")

        def on_strategy_result(self, event, *, hook, intents, traces, context):
            return RuntimeInstruction()

    class Strategy:
        strategy_id = "callback-control-strategy"

        def on_start(self, context) -> None:
            return None

        def on_data(self, context, event) -> None:
            dispatched.append(event.sequence)

        def on_end(self, context) -> None:
            return None

    callback = Callback()
    runtime = build_strategy_runtime_session(Strategy())
    first = make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=1, payload={}, producer="test")
    second = make_message("market", "quote", at=datetime(2026, 1, 1, tzinfo=timezone.utc), sequence=2, payload={}, producer="test")

    first_instruction = callback.on_message(first)
    if first_instruction.dispatch_strategy:
        runtime.process(first, hook=first_instruction.strategy_hook)
    assert dispatched == []
    assert not runtime.frame.finished

    second_instruction = callback.on_message(second)
    if second_instruction.action == "stop":
        runtime.frame.finished = True
    elif second_instruction.dispatch_strategy:
        runtime.process(second, hook=second_instruction.strategy_hook)
    assert dispatched == []
    assert runtime.frame.finished


def test_runtime_only_collects_intents_and_system_callback_owns_the_journal() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intent = target_position_intent(
        strategy_id="intent-owner-strategy",
        instrument_id="AAPL",
        market_id="binance.equity",
        target_quantity=Decimal("1"),
        at=at,
    )
    journal = IntentJournal()
    callback = _business_runtime(journal)

    callback.on_strategy_result(
        make_message("market", "quote", at=at, sequence=1, payload={}, producer="test"),
        hook="on_data",
        intents=(intent,),
        context=SimpleNamespace(now=at),
    )

    assert tuple(state.intent.intent_id for state in journal.list()) == (intent.intent_id,)


def test_strategy_intent_reaches_system_journal_after_runtime_dispatch() -> None:
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    journal = IntentJournal()
    intent = target_position_intent(
        strategy_id="runtime-intent-strategy",
        instrument_id="AAPL",
        target_quantity=Decimal("1"),
        at=at,
    )

    class Strategy:
        strategy_id = "runtime-intent-strategy"

        def on_start(self, context) -> None:
            return None

        def on_data(self, context, event) -> None:
            context.intent(intent)

        def on_end(self, context) -> None:
            return None

    callback = _business_runtime(journal)
    runtime = build_strategy_runtime_session(Strategy())
    event = make_message("market", "quote", at=at, sequence=1, payload={}, producer="test")
    cycle = runtime.process(event, hook="on_data")
    assert cycle.dispatched
    output = cycle.output
    callback.on_strategy_result(
        event,
        hook="on_data",
        intents=tuple(getattr(output, "intents", ())),
        context=getattr(output, "context", None),
    )

    assert len(journal.list(strategy_id="runtime-intent-strategy")) == 1
