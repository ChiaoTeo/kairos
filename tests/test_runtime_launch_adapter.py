from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
import json
import sys

from typer.testing import CliRunner

from kairospy.application.protocol import RuntimeEnvelope, RuntimeLine
from kairospy.application.modes import RuntimeMode
from kairospy.application.runtime import RuntimeLaunchSpec, RuntimeRunner
from kairospy.application.strategy import StrategyBase
from kairospy.surface.cli.app import execute_argv
from kairospy.surface.cli.commands.launch import launch_app


class RecordingStrategy(StrategyBase):
    strategy_id = "recording"

    def __init__(self) -> None:
        self.system_events = 0
        self.data_events = 0

    def on_system(self, context, signal) -> None:
        self.system_events += 1

    def on_data(self, context, signal) -> None:
        self.data_events += 1


def test_runtime_runner_pumps_start_event() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strategy = RecordingStrategy()

    result = RuntimeRunner.run_sync(
        RuntimeLaunchSpec(
            launch_id="launch-1",
            mode=RuntimeMode.BACKTEST,
            strategy=strategy,
            source=RuntimeLine((RuntimeEnvelope("market", "quote", now, 1, {"price": "100"}),)),
        )
    )

    assert result.launch_id == "launch-1"
    assert result.mode is RuntimeMode.BACKTEST
    assert result.runtime.strategy_id == "recording"
    assert result.runtime.event_count == 2
    assert strategy.system_events == 1
    assert strategy.data_events == 1


def test_launch_events_command_executes_jsonl_source(tmp_path, monkeypatch) -> None:
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
        launch_app,
        [
            "replay",
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
    assert '"launch_id": "kairos-launch"' in result.output
    assert '"event_count": 2' in result.output


def test_launch_system_rejects_legacy_strategy_command_options(tmp_path) -> None:
    result = CliRunner().invoke(
        launch_app,
        [
            "system",
            "--strategy",
            "strategy_mod:TestCliStrategy",
            "--commands",
            str(tmp_path / "commands.jsonl"),
        ],
    )

    assert result.exit_code != 0
    assert "No such option" in result.output or "No such command" in result.output


def test_launch_cli_command_is_not_registered() -> None:
    result = CliRunner().invoke(
        launch_app,
        ["cli", "--help"],
    )

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_launch_system_command_enqueues_builtin_system_command(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    root = tmp_path / ".kairos" / "launches"
    group = root / "system" / "kairos-system"
    instance = group / "instances" / "instance-1"
    instance.mkdir(parents=True)
    (group / "current.json").write_text(
        '{"launch_id":"kairos-system","mode":"system","launch_instance_id":"instance-1","directory":"' + str(instance) + '"}',
        encoding="utf-8",
    )
    (group / "state.json").write_text('{"launch_id":"kairos-system","mode":"system","mirrored_from":"' + str(instance) + '"}', encoding="utf-8")
    (instance / "state.json").write_text(
        json.dumps(
            {
                "launch_id": "kairos-system",
                "mode": "system",
                "phase": "running",
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    output = StringIO()

    exit_code = execute_argv(
        [
            "system",
            "command",
            "account.current",
            "--no-wait",
            "--format",
            "json",
        ],
        output,
    )

    assert exit_code == 0
    payload = output.getvalue()
    assert '"launch_id": "kairos-system"' in payload
    assert '"mode": "system"' in payload
    assert '"kind": "account.current"' in payload
