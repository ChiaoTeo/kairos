from __future__ import annotations

from datetime import datetime, timezone
import sys

from typer.testing import CliRunner

from kairospy.application.runtime import RuntimeEnvelope, RuntimeLine, RuntimeMode, RuntimeRunSpec, RuntimeRunner
from kairospy.application.strategy import StrategyBase
from kairospy.surface.products.run import run_app


class RecordingStrategy(StrategyBase):
    strategy_id = "recording"

    def __init__(self) -> None:
        self.system_events = 0
        self.data_events = 0

    def on_system(self, context, signal) -> None:
        self.system_events += 1
        context.control.request_pause(reason="test pause", request_id="pause-1")

    def on_data(self, context, signal) -> None:
        self.data_events += 1


def test_runtime_runner_pumps_start_event_and_records_controls() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strategy = RecordingStrategy()

    result = RuntimeRunner.run_sync(
        RuntimeRunSpec(
            run_id="run-1",
            mode=RuntimeMode.BACKTEST,
            strategy=strategy,
            source=RuntimeLine((RuntimeEnvelope("market", "quote", now, 1, {"price": "100"}),)),
        )
    )

    assert result.run_id == "run-1"
    assert result.mode is RuntimeMode.BACKTEST
    assert result.runtime.strategy_id == "recording"
    assert result.runtime.event_count == 2
    assert strategy.system_events == 1
    assert strategy.data_events == 1
    assert [request.request_id for request in result.controls.list()] == ["pause-1"]
    assert result.controls.list()[0].requested_at == now


def test_run_events_command_executes_jsonl_source(tmp_path, monkeypatch) -> None:
    strategy_path = tmp_path / "strategy_mod.py"
    strategy_path.write_text(
        "\n".join([
            "from kairospy.application.strategy import StrategyBase",
            "class CliStrategy(StrategyBase):",
            "    strategy_id = 'cli-strategy'",
            "    def on_data(self, context, signal):",
            "        return None",
        ]),
        encoding="utf-8",
    )
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"domain":"market","kind":"quote","time":"2026-01-01T00:00:00+00:00","payload":{"price":"100"}}\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("strategy_mod", None)

    result = CliRunner().invoke(
        run_app,
        [
            "events",
            "--strategy",
            "strategy_mod:CliStrategy",
            "--events",
            str(events_path),
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert '"run_id": "kairos-run"' in result.output
    assert '"event_count": 2' in result.output
