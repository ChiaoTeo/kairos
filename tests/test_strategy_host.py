from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
import pytest

from kairospy.application.launch import (
    LaunchIdentity,
    LaunchInstance,
    LaunchInstanceApplication,
)
from kairospy.application.strategy import StrategyApplication, StrategyLifecycle
from kairospy.application.strategy.services.ingress import StrategyEventIngress
from kairospy.application.account import AccountSegmentSnapshot, DataFreshness, SPOT
from kairospy.application.execution import ExecutionBacktestResult
from kairospy.domain_types import AccountId
from kairospy.application.market import MarketSnapshot, ObservationScope
from kairospy.application.market.events import MarketEventRecord
from kairospy.application.market.mapping import map_market_event
from kairospy.strategy import (
    BarEvent,
    ClockAdvance,
    ClockAdvancedEvent,
    EventMetadata,
    ExchangeId,
    InstrumentId,
    InstrumentRef,
    ListingId,
    Market,
    MarketId,
    MarketStatus,
    Strategy,
    StrategyCommand,
    SystemEvent,
    SystemNotice,
)
from kairospy.application.strategy.services import (
    InMemoryApplicationPorts,
    InMemoryMarketEventSource,
    InMemoryLifecycleJournal,
    InMemoryMarketSnapshotReader,
    StrategyControlServer,
    build_in_memory_strategy_applications,
)
from kairospy.strategy import StrategyState
from kairospy.application.market import EventStreamGap
from kairospy.infrastructure.transport.market import BarView, DecimalValue, QuoteView
from kairospy.application.system import UnixRestClient
from kairospy.strategy import StrategyLogger, StrategyOutput
from kairospy.strategy import CommandResult


_MARKET = Market(
    MarketId("market:test:BTCUSDT"),
    InstrumentRef(InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT"),
    ListingId("listing:test:BTCUSDT"),
    ExchangeId("exchange:test"),
    "BTCUSDT",
    "spot",
    status=MarketStatus.ACTIVE,
)


def EventEnvelope(
    stream_id: str,
    sequence: int,
    domain: str,
    kind: str,
    payload: object,
    occurred_at: datetime | None = None,
) -> object:
    """Test fixture adapter that builds the same transport views as production."""
    event_time = int(
        (occurred_at or datetime.now(timezone.utc)).timestamp() * 1_000_000_000
    )
    if domain == "data" and isinstance(payload, dict):
        symbol = str(payload.get("symbol", "BTCUSDT"))
        instrument_id = f"instrument:test:{symbol}"
        market_id = f"market:test:{symbol}"
        if kind == "bar":
            close = DecimalValue(int(payload.get("close", 100)), 0)
            payload = BarView(
                instrument_id,
                ObservationScope.market(market_id),
                "1m",
                close,
                close,
                close,
                close,
                None,
                event_time,
                "test",
                "provider",
            )
        elif kind == "quote":
            bid = DecimalValue(100, 0)
            ask = DecimalValue(101, 0)
            payload = QuoteView(
                instrument_id,
                ObservationScope.market(market_id),
                bid,
                None,
                ask,
                None,
                event_time,
                "test",
            )
    if domain == "clock" and kind == "advance":
        assert occurred_at is not None
        return ClockAdvancedEvent(
            ClockAdvance(occurred_at, str(payload.get("source", "runtime"))),
            EventMetadata(
                stream_id,
                sequence,
                producer="strategy.clock",
                occurred_at=occurred_at,
                occurred_at_unix_nanos=event_time,
            ),
        )
    if domain != "data":
        raise ValueError(f"unsupported test event domain: {domain}")
    return map_market_event(
        MarketEventRecord(stream_id, sequence, kind, payload, occurred_at)
    )


class UserStrategy(Strategy):
    strategy_id = "user-sma"

    def __init__(self) -> None:
        self.events: list[int] = []

    def on_start(self, context) -> None:
        context.market.subscribe_bars(
            _MARKET, timeframe="1m", source_id="market-source:primary"
        )

    def on_market(self, context, event) -> None:
        if not isinstance(event, BarEvent):
            return
        self.events.append(event.metadata.sequence)
        context.execution.target_position(
            event.data.instrument, Decimal("1"), account="main"
        )


class InvalidTypedHookStrategy(Strategy):
    strategy_id = "invalid-typed-hook"

    def on_quote(self, context, event):
        return "callbacks must not return values"


class RuntimeFactStrategy(UserStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.system_notices: list[SystemNotice] = []

    def on_system(self, context, event) -> None:
        self.system_notices.append(event.data)


class CommandStrategy(Strategy):
    strategy_id = "command-strategy"

    async def on_command(self, context, command: StrategyCommand) -> CommandResult:
        return CommandResult(
            command.request_id,
            "completed",
            result={"kind": command.kind, "source": command.source},
        )


class SiblingEventStrategy(Strategy):
    strategy_id = "sibling-events"

    def __init__(self) -> None:
        self.messages: list[str] = []

    def on_system(self, context, event) -> None:
        self.messages.append(event.data.message)


class TimerStrategy(Strategy):
    strategy_id = "timer-strategy"

    def __init__(self) -> None:
        self.clock_events: list[tuple[str, datetime]] = []

    def on_start(self, context) -> None:
        context.clock.every(
            "rebalance",
            "1h",
            start_at=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        )

    def on_clock(self, context, event) -> None:
        if event.kind == "timer":
            self.clock_events.append((event.data.timer_id, event.data.scheduled_at))


class FiniteReplayStream(InMemoryMarketEventSource):
    replayable = True

    async def replay_from(self, after_sequence: int = 0):
        while self._events:
            event = self._events.popleft()
            if event.metadata.sequence > after_sequence:
                yield event


class RecoveringSnapshotReader:
    def __init__(self) -> None:
        self.read_count = 0

    def read(self, view_key: str) -> MarketSnapshot:
        self.read_count += 1
        return MarketSnapshot(
            view_key,
            f"snapshot-{self.read_count}",
            "market-actor",
            self.read_count,
        )


class GapThenRecoveryStream:
    stream_id = "market.events"

    def __init__(self) -> None:
        self.calls = 0

    async def replay_from(self, after_sequence: int = 0):
        self.calls += 1
        if self.calls == 1:
            raise EventStreamGap(self.stream_id, after_sequence + 1, after_sequence + 2)
        yield EventEnvelope(
            self.stream_id,
            after_sequence + 1,
            "data",
            "quote",
            {"close": 100},
            datetime.now(timezone.utc),
        )


class FailingReadinessStream(InMemoryMarketEventSource):
    def check_ready(self) -> None:
        raise RuntimeError("Market event source is unavailable")


class FakeStrategyBacktestDriver:
    def __init__(self, *, apply_market=None, mark_account=None) -> None:
        self._apply_market = apply_market or (lambda event: ExecutionBacktestResult(()))
        self._mark_account = mark_account or (lambda event: None)

    def advance_time(self, event_time_unix_nanos: int) -> None:
        return None

    def apply_market(self, event):
        result = self._apply_market(event)
        return ExecutionBacktestResult(()) if result is None else result

    def mark_account(self, event):
        return self._mark_account(event)


def _strategy_application_arguments(
    bus,
    snapshots,
    stream,
    *,
    strategy_id: str,
    instance_id: str,
):
    reference, market, account, risk, execution = build_in_memory_strategy_applications(
        bus,
        snapshots,
        stream,
        strategy_id=strategy_id,
        instance_id=instance_id,
    )
    return {
        "reference": reference,
        "market": market,
        "account": account,
        "risk": risk,
        "execution": execution,
    }


def _host(
    tmp_path: Path,
    logger: StrategyLogger | None = None,
    *,
    params: dict[str, object] | None = None,
    strategy: Strategy | None = None,
):
    bus = InMemoryApplicationPorts()
    stream = InMemoryMarketEventSource("market.events")
    snapshots = InMemoryMarketSnapshotReader(
        {
            "market.current": MarketSnapshot(
                "market.current",
                "snapshot-1",
                "market-actor",
                1,
            ),
        }
    )
    strategy = strategy or UserStrategy()
    host = StrategyApplication(
        strategy,
        launch_id="btc-paper",
        instance_id="instance-1",
        **_strategy_application_arguments(
            bus,
            snapshots,
            stream,
            strategy_id=strategy.strategy_id,
            instance_id="instance-1",
        ),
        journal=InMemoryLifecycleJournal(),
        logger=logger,
        params=params,
    )
    return host, strategy, bus, stream


def _timer_host(tmp_path: Path):
    bus = InMemoryApplicationPorts()
    stream = InMemoryMarketEventSource("market.events")
    snapshots = InMemoryMarketSnapshotReader(
        {
            "market.current": MarketSnapshot(
                "market.current",
                "snapshot-1",
                "market-actor",
                1,
            ),
        }
    )
    strategy = TimerStrategy()
    host = StrategyApplication(
        strategy,
        launch_id="timer-launch",
        instance_id="instance-1",
        **_strategy_application_arguments(
            bus,
            snapshots,
            stream,
            strategy_id=strategy.strategy_id,
            instance_id="instance-1",
        ),
        journal=InMemoryLifecycleJournal(),
    )
    return host, strategy, bus


def test_strategy_clock_fires_deterministic_catch_up_timers_before_market_event(
    tmp_path: Path,
) -> None:
    host, strategy, bus = _timer_host(tmp_path)
    host.start()
    bus.resolve(bus.requests[0].request_id) if bus.requests else None
    host.refresh()
    host.enable()

    host.dispatch(
        EventEnvelope(
            "market.events",
            1,
            "data",
            "bar",
            {"close": 101},
            datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
        )
    )
    assert strategy.clock_events == [
        ("rebalance", datetime(2024, 1, 1, 1, tzinfo=timezone.utc)),
        ("rebalance", datetime(2024, 1, 1, 2, tzinfo=timezone.utc)),
    ]
    assert host.context.clock.now == datetime(2024, 1, 1, 2, tzinfo=timezone.utc)


def test_multiple_callbacks_from_one_source_record_share_sequence_safely(
    tmp_path: Path,
) -> None:
    strategy = SiblingEventStrategy()
    application, _, _, _ = _host(tmp_path, strategy=strategy)
    application.start()
    application.enable()
    metadata = EventMetadata("system:record", 7, producer="system")

    application.dispatch(SystemEvent(SystemNotice("first", "first"), metadata))
    application.dispatch(SystemEvent(SystemNotice("second", "second"), metadata))

    assert strategy.messages == ["first", "second"]
    assert [item["source_sequence"] for item in application.event_trace] == [7, 7]
    assert [item["trace_sequence"] for item in application.event_trace] == [1, 2]


def test_typed_market_hook_preserves_callback_return_validation(tmp_path: Path) -> None:
    strategy = InvalidTypedHookStrategy()
    application, _, _, _ = _host(tmp_path, strategy=strategy)
    application.start()
    application.enable()

    with pytest.raises(TypeError, match="on_market must return None"):
        application.dispatch(
            EventEnvelope(
                "market.events",
                1,
                "data",
                "quote",
                {"symbol": "AAPL"},
            )
        )

    assert application.status.state is StrategyLifecycle.FAILED


def test_replay_dispatch_visits_timer_times_inside_a_market_gap(tmp_path: Path) -> None:
    host, strategy, bus = _timer_host(tmp_path)
    host.start()
    host.refresh()
    host.enable()

    host._dispatch_replay_event(
        EventEnvelope(
            "market.events",
            1,
            "data",
            "bar",
            {"close": 101},
            datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
        )
    )

    assert [scheduled for _, scheduled in strategy.clock_events] == [
        datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
    ]


def test_strategy_clock_can_fire_without_a_market_event(tmp_path: Path) -> None:
    host, strategy, bus = _timer_host(tmp_path)
    host.start()
    host.refresh()
    host.enable()

    host.advance_time(datetime(2024, 1, 1, 1, tzinfo=timezone.utc))

    assert strategy.clock_events == [
        ("rebalance", datetime(2024, 1, 1, 1, tzinfo=timezone.utc))
    ]
    assert host.context.now == datetime(2024, 1, 1, 1, tzinfo=timezone.utc)


def test_external_clock_event_advances_context_business_time(tmp_path: Path) -> None:
    host, strategy, bus = _timer_host(tmp_path)
    host.start()
    host.refresh()
    host.enable()

    host.dispatch(
        EventEnvelope(
            "market.events",
            1,
            "clock",
            "advance",
            {"source": "replay"},
            datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        )
    )

    assert strategy.clock_events == [
        ("rebalance", datetime(2024, 1, 1, 1, tzinfo=timezone.utc))
    ]


def test_live_market_events_do_not_move_business_time_backwards(tmp_path: Path) -> None:
    host, strategy, bus, _ = _host(tmp_path)
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    newer = datetime(2024, 1, 1, 2, tzinfo=timezone.utc)
    older = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)

    host.dispatch(EventEnvelope("market.events", 1, "data", "bar", {}, newer))
    host.dispatch(EventEnvelope("market.events", 2, "data", "bar", {}, older))

    assert strategy.events == [1, 2]
    assert host.context.clock.now == newer


def test_replay_driver_fires_each_timer_in_empty_market_gap(tmp_path: Path) -> None:
    bus = InMemoryApplicationPorts()
    stream = FiniteReplayStream("market.events")
    snapshots = InMemoryMarketSnapshotReader(
        {
            "market.current": MarketSnapshot(
                "market.current", "snapshot-1", "market-actor", 1
            )
        }
    )
    strategy = TimerStrategy()
    host = StrategyApplication(
        strategy,
        launch_id="replay-driver",
        instance_id="instance-1",
        **_strategy_application_arguments(
            bus,
            snapshots,
            stream,
            strategy_id=strategy.strategy_id,
            instance_id="instance-1",
        ),
        journal=InMemoryLifecycleJournal(),
        replay_end=datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
    )
    host.start()
    host.refresh()
    host.enable()
    stream.append(
        EventEnvelope(
            "market.events",
            1,
            "data",
            "bar",
            {},
            datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )
    stream.append(
        EventEnvelope(
            "market.events",
            2,
            "data",
            "bar",
            {},
            datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
        )
    )

    asyncio.run(host.run())

    assert [scheduled for _, scheduled in strategy.clock_events] == [
        datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
    ]


def test_strategy_dependencies_are_declared_through_context_bus(tmp_path: Path) -> None:
    host, strategy, bus, stream = _host(tmp_path)

    status = host.start()

    assert status.state is StrategyLifecycle.WAITING_FOR_DEPENDENCIES
    assert status.readiness.value == "waiting_for_dependencies"
    assert status.subscription_count == 1
    assert status.active_subscription_count == 0
    assert status.subscriptions[0]["status"] == "pending"
    assert len(bus.requests) == 1
    assert bus.requests[0].operation == "market.subscribe"
    assert bus.requests[0].payload.source_id == "market-source:primary"
    assert strategy.events == []

    bus.resolve(bus.requests[0].request_id)
    assert host.refresh().state is StrategyLifecycle.READY
    assert host.status.readiness.value == "ready"
    assert host.status.active_subscription_count == 1
    assert host.status.subscriptions[0]["status"] == "ready"
    assert host.enable().state is StrategyLifecycle.RUNNING
    assert host.status.data_health.value == "waiting_for_data"

    host.dispatch(
        EventEnvelope(
            "market.events",
            1,
            "data",
            "bar",
            {"close": 100},
            datetime.now(timezone.utc),
        )
    )
    assert strategy.events == [1]
    assert bus.requests[1].operation == "intent.target_position"
    assert bus.requests[1].payload.instrument_id == "instrument:test:BTCUSDT"
    assert bus.requests[1].payload.source_event_sequence == 1
    assert bus.requests[1].payload.source_event_time_unix_nanos is not None


def test_market_subscription_uses_reference_market_route(tmp_path: Path) -> None:
    host, _, bus, _ = _host(tmp_path)
    market = Market(
        MarketId("market:exchange:nasdaq:equity:AAPL"),
        InstrumentRef(InstrumentId("instrument:equity:US:AAPL:common"), "AAPL"),
        ListingId("listing:exchange:nasdaq:equity:AAPL:USD"),
        ExchangeId("exchange:nasdaq"),
        "equity",
        "AAPL",
        status=MarketStatus.ACTIVE,
    )

    host.context.market.subscribe_quotes(market)

    request = bus.requests[-1].payload
    assert request.subject == str(market.id)
    assert request.identity == str(market.id)
    assert request.source_id is None
    assert request.exchange is None
    assert request.market_type is None
    assert request.params == {"market_id": str(market.id)}


def test_strategy_start_fails_when_enabled_business_event_source_is_not_ready(
    tmp_path: Path,
) -> None:
    bus = InMemoryApplicationPorts()
    stream = FailingReadinessStream("market.events")
    snapshots = InMemoryMarketSnapshotReader(
        {
            "market.current": MarketSnapshot(
                "market.current", "snapshot-1", "market-actor", 1
            )
        }
    )
    strategy = UserStrategy()
    application = StrategyApplication(
        strategy,
        launch_id="readiness-launch",
        instance_id="instance-1",
        **_strategy_application_arguments(
            bus,
            snapshots,
            stream,
            strategy_id=strategy.strategy_id,
            instance_id="instance-1",
        ),
        journal=InMemoryLifecycleJournal(),
    )

    with pytest.raises(RuntimeError, match="Market event source is unavailable"):
        application.start()

    assert application.status.state is StrategyLifecycle.FAILED


def test_strategy_stop_releases_all_market_leases_after_on_end(tmp_path: Path) -> None:
    host, _, bus, _ = _host(tmp_path)
    host.start()
    subscription_id = bus.requests[0].request_id
    bus.resolve(subscription_id)
    host.refresh()
    host.enable()

    host.stop()

    assert bus.requests[-1].operation == "market.release_owner"
    assert host.status.subscription_count == 0
    assert host.status.active_subscription_count == 0
    assert host.status.subscriptions[0]["status"] == "removed"


def test_strategy_process_close_retries_owner_cleanup_idempotently(
    tmp_path: Path,
) -> None:
    host, _, bus, _ = _host(tmp_path)
    host.start()

    host.close()
    host.close()

    releases = [
        request
        for request in bus.requests
        if request.operation == "market.release_owner"
    ]
    assert len(releases) == 1


def test_strategy_start_failure_releases_owner_after_subscription(
    tmp_path: Path,
) -> None:
    host, _, bus, _ = _host(tmp_path)

    def fail_status(request_id: str):
        raise RuntimeError(f"Market unavailable for {request_id}")

    bus.status = fail_status  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Market unavailable"):
        host.start()

    assert host.status.state is StrategyLifecycle.FAILED
    assert bus.requests[-1].operation == "market.release_owner"


def test_strategy_state_is_json_only_versioned_and_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "strategy-state.json"
    state = StrategyState(path)
    state.set_int("window", 3)
    state.set_decimal("threshold", Decimal("1.25"))
    state.checkpoint()

    restored = StrategyState(path)

    assert restored.schema_version == 1
    assert restored.get_int("window") == 3
    assert restored.get_decimal("threshold") == Decimal("1.25")
    with pytest.raises(Exception, match="expected=int"):
        restored.get_int("threshold")


def test_strategy_context_exposes_immutable_identity_and_params(tmp_path: Path) -> None:
    host, _, _, _ = _host(tmp_path, params={"window": 20})

    assert host.context.identity.strategy_id == "user-sma"
    assert host.context.identity.launch_id == "btc-paper"
    assert host.context.identity.instance_id == "instance-1"
    assert host.context.params == {"window": 20}
    with pytest.raises(TypeError):
        host.context.params["window"] = 30  # type: ignore[index]


def test_execution_application_uses_strategy_scoped_command_surface(
    tmp_path: Path,
) -> None:
    host, _, bus, _ = _host(tmp_path)

    receipt = host.context.execution.target_position(
        _MARKET.instrument, Decimal("2"), account="main"
    )

    assert receipt.request_id == bus.requests[-1].request_id
    assert bus.requests[-1].operation == "intent.target_position"
    assert bus.requests[-1].strategy_id == "user-sma"


def test_strategy_does_not_use_snapshot_to_hide_event_gap(tmp_path: Path) -> None:
    bus = InMemoryApplicationPorts()
    snapshots = RecoveringSnapshotReader()
    stream = GapThenRecoveryStream()
    strategy = RuntimeFactStrategy()
    host = StrategyApplication(
        strategy,
        launch_id="gap-launch",
        instance_id="gap-instance",
        **_strategy_application_arguments(
            bus,
            snapshots,
            stream,
            strategy_id=strategy.strategy_id,
            instance_id="gap-instance",
        ),
        journal=InMemoryLifecycleJournal(),
    )
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()

    with pytest.raises(RuntimeError, match="market event source failed") as raised:
        asyncio.run(host.run())

    assert isinstance(raised.value.__cause__, EventStreamGap)
    assert stream.calls == 1
    assert snapshots.read_count == 0
    assert host.status.state is StrategyLifecycle.FAILED
    assert strategy.system_notices[-1].code == "event_source_failed"
    assert strategy.system_notices[-1].details["domain"] == "market"


@pytest.mark.parametrize("failed_domain", ["account", "risk", "execution"])
def test_non_market_event_gap_also_fails_strategy_lifecycle(
    tmp_path: Path, failed_domain: str
) -> None:
    class EmptySource:
        async def events(self):
            if False:
                yield None

    class GapSource:
        async def events(self):
            raise RuntimeError(f"{failed_domain} event stream is not contiguous")
            yield None

    host, strategy, bus, _ = _host(tmp_path, strategy=RuntimeFactStrategy())
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    sources = {domain: EmptySource() for domain in ("market", "account", "risk", "execution")}
    sources[failed_domain] = GapSource()
    host.ingress = StrategyEventIngress(**sources)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=f"{failed_domain} event source failed"):
        asyncio.run(host.run())

    assert host.status.state is StrategyLifecycle.FAILED
    assert strategy.system_notices[-1].code == "event_source_failed"
    assert strategy.system_notices[-1].details["domain"] == failed_domain


def test_strategy_stop_dispatches_shutdown_system_fact(tmp_path: Path) -> None:
    strategy = RuntimeFactStrategy()
    host, _, bus, _ = _host(tmp_path, strategy=strategy)
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()

    host.stop()

    assert strategy.system_notices[-1].code == "strategy_shutdown"
    assert host.status.state is StrategyLifecycle.STOPPED


def test_strategy_logs_include_system_and_event_time(tmp_path: Path) -> None:
    output = StringIO()
    event_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    host, strategy, bus, _ = _host(
        tmp_path,
        StrategyLogger(fields={"component": "strategy"}, stream=output),
    )

    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    strategy.log_on_market = True
    host.dispatch(EventEnvelope("market.events", 1, "data", "quote", {}, event_time))
    assert host.status.first_event_received is True
    assert host.status.data_health.value == "healthy"
    assert host.status.last_event_time == event_time
    assert host.status.event_count == 1
    assert host.status.last_event_kind == "quote"

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    dispatch = next(
        record for record in records if record.get("event") == "strategy_on_market"
    )
    assert dispatch["system_time"]
    assert dispatch["event_time"] == event_time.isoformat()
    assert dispatch["event_time_source"] == "market_event"
    assert dispatch["event_sequence"] == 1
    assert dispatch["data"]["event_kind"] == "quote"
    requested = next(
        record
        for record in records
        if record.get("event") == "market_subscription_requested"
    )
    assert requested["data"]["market_type"] is None
    assert any(
        record.get("event") == "market_subscriptions_active" for record in records
    )
    assert any(record.get("event") == "first_data_event_received" for record in records)


def test_legacy_print_is_wrapped_with_event_context() -> None:
    output = StringIO()
    logger = StrategyLogger(stream=output)
    legacy_stdout = StrategyOutput(logger, source="stdout")
    event_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with logger.bind_event(
        event_time=event_time,
        event_time_source="market_event",
        event_sequence=7,
    ):
        legacy_stdout.write("user strategy output")
        legacy_stdout.flush()

    record = json.loads(output.getvalue())
    assert record["message"] == "user strategy output"
    assert record["data"]["source"] == "stdout"
    assert record["event_time"] == event_time.isoformat()
    assert record["event_sequence"] == 7


def test_strategy_start_does_not_join_events_through_snapshot(tmp_path: Path) -> None:
    host, _, bus, _ = _host(tmp_path)
    host.start()
    bus.resolve("user-sma:instance-1:market.subscribe:0:1")
    assert host.refresh().state is StrategyLifecycle.READY


def test_launch_instance_owns_strategy_lifecycle(tmp_path: Path) -> None:
    host, _, bus, _ = _host(tmp_path)
    instance = LaunchInstance(
        LaunchIdentity("btc-paper", "paper"),
        "instance-1",
        tmp_path / "instances" / "instance-1",
        tmp_path / "instances" / "instance-1" / "control.sock",
    )
    application = LaunchInstanceApplication(instance, host)

    application.start()
    bus.resolve(bus.requests[0].request_id)
    application.refresh()
    assert application.status()["launch_id"] == "btc-paper"
    assert application.status()["instance_id"] == "instance-1"
    assert application.enable().state is StrategyLifecycle.RUNNING
    assert application.stop().state is StrategyLifecycle.STOPPED
    assert application.instance.state.value == "stopped"


def test_strategy_control_uses_instance_unix_rest_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_tcp_keepalive(_transport: asyncio.Transport) -> None:
        raise AssertionError("TCP keepalive must not be applied to an AF_UNIX socket")

    monkeypatch.setattr("aiohttp.web_protocol.tcp_keepalive", reject_tcp_keepalive)

    async def scenario() -> None:
        host, _, bus, _ = _host(tmp_path)
        socket = Path(f"/tmp/kairos-strategy-{os.getpid()}.sock")
        server = StrategyControlServer(host, socket)
        await server.start()
        try:
            host.start()
            bus.resolve(bus.requests[0].request_id)
            host.refresh()
            status = await UnixRestClient(socket).request("GET", "/v1/health")
            assert status["launch_id"] == "btc-paper"
            assert status["status"] == "ready"
            assert status["readiness"] == "ready"
            assert status["data_health"] == "not_started"
            assert status["subscription_count"] == 1
            assert status["subscriptions"][0]["status"] == "ready"
            enabled = await UnixRestClient(socket).request("POST", "/v1/enable")
            assert enabled["status"] == "running"
            stopped = await UnixRestClient(socket).request("POST", "/v1/stop")
            assert stopped["status"] == "stopped"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_strategy_control_dispatches_command_to_optional_strategy_hook(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        host, _, bus, _ = _host(tmp_path)
        socket = Path(f"/tmp/kairos-strategy-command-{os.getpid()}.sock")
        server = StrategyControlServer(host, socket)
        await server.start()
        try:
            host.start()
            bus.resolve(bus.requests[0].request_id)
            host.refresh()
            host.enable()
            result = await UnixRestClient(socket).request(
                "POST",
                "/v1/command",
                {
                    "request_id": "command-1",
                    "kind": "interactive.python",
                    "source": "print('not supported by this strategy')",
                },
            )
            assert result["status"] == "rejected"
            assert result["error_code"] == "unsupported_command"
            assert result["request_id"] == "command-1"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_user_strategy_can_implement_on_command(tmp_path: Path) -> None:
    strategy = CommandStrategy()
    host, _, _, _ = _host(tmp_path, strategy=strategy)
    host.start()
    host.enable()
    result = asyncio.run(
        host.command(StrategyCommand("command-2", "custom.command", "42"))
    )
    assert result.status == "completed"
    assert result.result["source"] == "42"


def test_strategy_host_consumes_instance_event_stream(tmp_path: Path) -> None:
    async def scenario() -> None:
        host, strategy, bus, stream = _host(tmp_path)
        host.start()
        bus.resolve(bus.requests[0].request_id)
        host.refresh()
        host.enable()
        task = asyncio.create_task(host.run())
        stream.append(EventEnvelope("market.events", 1, "data", "bar", {"close": 101}))
        for _ in range(20):
            if strategy.events == [1]:
                break
            await asyncio.sleep(0)
        assert strategy.events == [1]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_strategy_can_enable_on_market_logging_at_runtime(tmp_path: Path) -> None:
    output = StringIO()
    logger = StrategyLogger(stream=output)
    host, strategy, bus, _ = _host(tmp_path, logger=logger)
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()

    strategy.log_on_market = True
    host.dispatch(
        EventEnvelope(
            "market.events",
            1,
            "data",
            "quote",
            {"symbol": "AAPL", "bid": "100.0"},
        )
    )

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    event = next(
        record for record in records if record.get("event") == "strategy_on_market"
    )
    assert event["data"]["event_kind"] == "quote"
    assert "AAPL" in event["data"]["event_payload"]


def test_strategy_on_market_logging_is_disabled_by_default(tmp_path: Path) -> None:
    output = StringIO()
    host, _, bus, _ = _host(tmp_path, logger=StrategyLogger(stream=output))
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    host.dispatch(
        EventEnvelope("market.events", 1, "data", "quote", {"symbol": "AAPL"})
    )

    assert '"event":"strategy_on_market"' not in output.getvalue()
    assert '"message":"dispatch on_market"' not in output.getvalue()


def test_backtest_quote_callbacks_bracket_strategy_and_record_equity(
    tmp_path: Path,
) -> None:
    host, strategy, bus, stream = _host(tmp_path)
    calls: list[str] = []

    host.backtest = FakeStrategyBacktestDriver(
        apply_market=lambda event: calls.append("execution"),
        mark_account=lambda event: (
            calls.append("account")
            or AccountSegmentSnapshot(
                AccountId("main"),
                SPOT,
                "paper",
                "paper",
                "no_margin",
                Decimal("101"),
                (),
                (),
                DataFreshness.FRESH,
                1,
            )
        ),
    )
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    event = EventEnvelope(
        "market.events",
        1,
        "data",
        "quote",
        {
            "instrument_id": "BTCUSDT",
            "bid_price": "100",
            "ask_price": "102",
            "event_time_unix_nanos": 1,
        },
    )
    host.dispatch(event)
    assert calls == ["execution", "account"]
    assert host.equity_curve[-1]["snapshot"].equity == Decimal("101")


def test_bar_backtest_callbacks_use_previous_completed_bar_for_execution(
    tmp_path: Path,
) -> None:
    host, _, bus, _ = _host(tmp_path)
    calls: list[tuple[str, int]] = []
    host.backtest = FakeStrategyBacktestDriver(
        apply_market=lambda event: calls.append(("execution", event.metadata.sequence)),
        mark_account=lambda event: calls.append(("account", event.metadata.sequence)),
    )
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()

    def bar(sequence: int, hour: int) -> EventEnvelope:
        return EventEnvelope(
            "market.events",
            sequence,
            "data",
            "bar",
            {"close": 100 + sequence},
            datetime(2024, 1, 1, hour, tzinfo=timezone.utc),
        )

    host.dispatch(bar(1, 10))
    assert calls == []
    host.dispatch(bar(2, 11))
    assert calls == [("execution", 1), ("account", 1)]
    host.stop()
    assert calls == [("execution", 1), ("account", 1), ("account", 2)]
