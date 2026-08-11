from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path

from kairospy.application.launch import (
    LaunchIdentity,
    LaunchInstance,
    LaunchInstanceApplication,
)
from kairospy.application.strategy import StrategyHost, StrategyLifecycle
from kairospy.application.strategy.domain.messages import SnapshotEnvelope
from kairospy.strategy import (
    EventEnvelope,
    StrategyBase,
)
from kairospy.application.strategy.services import (
    InMemoryContextBus,
    InMemoryEventStream,
    InMemoryLifecycleJournal,
    InMemorySnapshotReader,
    StrategyClientBundle,
    StrategyControlServer,
)
from kairospy.infrastructure.contracts.market import EventStreamGap
from kairospy.application.system import UnixRestClient
from kairospy.strategy import StrategyLogger, StrategyOutput


class UserStrategy(StrategyBase):
    strategy_id = "user-sma"

    def __init__(self) -> None:
        self.events: list[int] = []

    def on_start(self, context) -> None:
        context.subscribe("market.BTCUSDT", selectors=("bar:1m",))

    def on_data(self, context, event) -> None:
        self.events.append(event.sequence)
        context.target_position("BTCUSDT", 1)


class RecoveringSnapshotReader:
    def __init__(self) -> None:
        self.read_count = 0

    def read(self, view_key: str) -> SnapshotEnvelope:
        self.read_count += 1
        return SnapshotEnvelope(
            view_key,
            f"snapshot-{self.read_count}",
            "market-actor",
            "market-events",
            0 if self.read_count == 1 else 1,
            self.read_count,
            {"BTCUSDT": 100},
        )


class GapThenRecoveryStream:
    stream_id = "market-events"

    def __init__(self) -> None:
        self.calls = 0

    def can_join(self, event_sequence: int) -> bool:
        return True

    async def events(self, after_sequence: int = 0):
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


def _host(tmp_path: Path, logger: StrategyLogger | None = None):
    bus = InMemoryContextBus()
    stream = InMemoryEventStream("market-events")
    snapshots = InMemorySnapshotReader(
        {
            "market.current": SnapshotEnvelope(
                "market.current",
                "snapshot-1",
                "market-actor",
                "market-events",
                0,
                1,
                {"BTCUSDT": 100},
            ),
        }
    )
    strategy = UserStrategy()
    host = StrategyHost(
        strategy,
        launch_id="btc-paper",
        instance_id="instance-1",
        clients=StrategyClientBundle(
            commands=bus,
            market_commands=bus,  # In-memory bus is the test command capability.
            execution_commands=bus,
            market_snapshots=snapshots,
            market_events=stream,
        ),
        journal=InMemoryLifecycleJournal(),
        logger=logger,
    )
    return host, strategy, bus, stream


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
            "market-events",
            1,
            "data",
            "bar",
            {"close": 100},
            datetime.now(timezone.utc),
        )
    )
    assert strategy.events == [1]
    assert bus.requests[1].operation == "intent.target_position"
    assert bus.requests[1].payload.instrument_id == "BTCUSDT"


def test_strategy_recovers_market_snapshot_after_event_gap(tmp_path: Path) -> None:
    bus = InMemoryContextBus()
    snapshots = RecoveringSnapshotReader()
    stream = GapThenRecoveryStream()
    host = StrategyHost(
        UserStrategy(),
        launch_id="gap-launch",
        instance_id="gap-instance",
        clients=StrategyClientBundle(
            commands=bus,
            market_commands=bus,
            execution_commands=bus,
            market_snapshots=snapshots,
            market_events=stream,
        ),
        journal=InMemoryLifecycleJournal(),
    )
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()

    asyncio.run(host.run())

    assert stream.calls == 2
    assert snapshots.read_count == 2
    assert host.status.event_sequence == 2


def test_strategy_logs_include_system_and_event_time(tmp_path: Path) -> None:
    output = StringIO()
    event_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    host, _, bus, _ = _host(
        tmp_path,
        StrategyLogger(fields={"component": "strategy"}, stream=output),
    )

    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    host.dispatch(EventEnvelope("market-events", 1, "data", "quote", {}, event_time))
    assert host.status.first_event_received is True
    assert host.status.data_health.value == "healthy"
    assert host.status.last_event_time == event_time
    assert host.status.event_count == 1
    assert host.status.last_event_kind == "quote"

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    dispatch = next(
        record for record in records if record["message"] == "dispatch on_data"
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
    submitted = next(
        record
        for record in records
        if record.get("event") == "strategy_command_submitted"
    )
    assert submitted["operation"] == "market.subscribe"
    assert submitted["data"]["subject"] == "market.BTCUSDT"
    result = next(
        record for record in records if record.get("event") == "strategy_command_result"
    )
    assert result["data"]["command_status"] == "pending"


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


def test_strategy_cannot_run_until_snapshot_watermark_is_joined(tmp_path: Path) -> None:
    host, _, bus, stream = _host(tmp_path)
    stream.first_sequence = 20
    host.start()
    bus.resolve("user-sma:instance-1:market.subscribe:0:1")

    try:
        host.refresh()
    except RuntimeError as error:
        assert "watermark" in str(error)
    else:
        raise AssertionError("strategy should reject an unjoinable snapshot watermark")


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


def test_strategy_control_uses_instance_unix_rest_socket(tmp_path: Path) -> None:
    async def scenario() -> None:
        host, _, bus, _ = _host(tmp_path)
        socket = Path(f"/tmp/kairos-strategy-{os.getpid()}.sock")
        server = StrategyControlServer(host, socket)
        await server.start()
        try:
            host.start()
            bus.resolve(bus.requests[0].request_id)
            host.refresh()
            status = await UnixRestClient(socket).request("GET", "/v1/status")
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


def test_strategy_host_consumes_instance_event_stream(tmp_path: Path) -> None:
    async def scenario() -> None:
        host, strategy, bus, stream = _host(tmp_path)
        host.start()
        bus.resolve(bus.requests[0].request_id)
        host.refresh()
        host.enable()
        task = asyncio.create_task(host.run())
        stream.append(EventEnvelope("market-events", 1, "data", "bar", {"close": 101}))
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


def test_backtest_callbacks_run_after_strategy_intent_and_record_equity(
    tmp_path: Path,
) -> None:
    host, strategy, bus, stream = _host(tmp_path)
    calls: list[str] = []

    host.clients = host.clients.__class__(
        commands=host.clients.commands,
        market_commands=host.clients.market_commands,
        execution_commands=host.clients.execution_commands,
        market_snapshots=host.clients.market_snapshots,
        market_events=host.clients.market_events,
        reference=host.clients.reference,
        backtest_market=lambda event: calls.append("execution"),
        backtest_account_mark=lambda event: (
            calls.append("account") or {"snapshot": {"equity": "101"}}
        ),
    )
    host.start()
    bus.resolve(bus.requests[0].request_id)
    host.refresh()
    host.enable()
    event = EventEnvelope(
        "market-events",
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
    assert bus.requests[-1].operation == "intent.target_position"
    assert calls == ["execution", "account"]
    assert host.equity_curve[-1]["snapshot"] == {"equity": "101"}
