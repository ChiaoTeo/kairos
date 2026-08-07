from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path

from kairospy.application.launch import LaunchIdentity, LaunchInstance, LaunchInstanceApplication
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
    StrategyControlServer,
)
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


def _host(tmp_path: Path, logger: StrategyLogger | None = None):
    bus = InMemoryContextBus()
    stream = InMemoryEventStream("market-events")
    snapshots = InMemorySnapshotReader({
        "market.current": SnapshotEnvelope(
            "market.current", "snapshot-1", "market-actor", "market-events", 0, 1, {"BTCUSDT": 100},
        ),
    })
    strategy = UserStrategy()
    host = StrategyHost(
        strategy,
        launch_id="btc-paper",
        instance_id="instance-1",
        bus=bus,
        snapshots=snapshots,
        stream=stream,
        journal=InMemoryLifecycleJournal(),
        logger=logger,
    )
    return host, strategy, bus, stream


def test_strategy_dependencies_are_declared_through_context_bus(tmp_path: Path) -> None:
    host, strategy, bus, stream = _host(tmp_path)

    status = host.start()

    assert status.state is StrategyLifecycle.WAITING_FOR_DEPENDENCIES
    assert len(bus.requests) == 1
    assert bus.requests[0].operation == "market.subscribe"
    assert strategy.events == []

    bus.resolve(bus.requests[0].request_id)
    assert host.refresh().state is StrategyLifecycle.READY
    assert host.enable().state is StrategyLifecycle.RUNNING

    host.dispatch(EventEnvelope("market-events", 1, "data", "bar", {"close": 100}, datetime.now(timezone.utc)))
    assert strategy.events == [1]
    assert bus.requests[1].operation == "intent.target_position"
    assert bus.requests[1].payload.instrument_id == "BTCUSDT"


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

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    dispatch = next(record for record in records if record["message"] == "dispatch on_data")
    assert dispatch["system_time"]
    assert dispatch["event_time"] == event_time.isoformat()
    assert dispatch["event_time_source"] == "market_event"
    assert dispatch["event_sequence"] == 1


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
    bus.resolve("user-sma:market.subscribe:0:1")

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
