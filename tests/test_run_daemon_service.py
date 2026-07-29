from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest
from typer.testing import CliRunner

from kairospy.application.runtime import RuntimeMode
from kairospy.application.system.control.daemon import RunAlreadyActiveError, RunDaemonService
from kairospy.application.system.control.registry import RunRegistry
from kairospy.infrastructure.data import DataStore
from kairospy.surface.cli import app
from kairospy.surface.cli.commands.run import run_app


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_run_daemon_service_runs_backtest_foreground_and_writes_state(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = RunDaemonService(root).run_foreground(mode="backtest", config_path=config_path)

    assert result.phase == "stopped"
    assert result.run_instance_id
    assert result.result["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "state.json").exists()
    assert (root / "backtest" / "bt-1" / "events.jsonl").exists()
    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == result.run_instance_id
    assert (root / "backtest" / "bt-1" / "instances" / result.run_instance_id / "state.json").exists()
    assert (root / "backtest" / "bt-1" / "instances" / result.run_instance_id / "summary.json").exists()
    assert (root / "backtest" / "bt-1" / "instances" / result.run_instance_id / "account" / "current.json").exists()
    record = RunRegistry(root).list(mode="backtest", run_id="bt-1")[0].to_dict()
    assert record["phase"] == "stopped"
    assert record["heartbeat_at"] is not None
    assert record["result"]["event_count"] == 2
    assert record["context"]["config_file"] == str(config_path)


def test_run_daemon_start_foreground_command_runs_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--foreground", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "stopped"
    assert payload["run_instance_id"]
    assert payload["result"]["event_count"] == 2
    assert (root / "backtest" / "bt-1" / "summary.json").exists()


def test_run_daemon_start_uses_workspace_default_text_format(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n",
        encoding="utf-8",
    )
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        app,
        ["run", "daemon", "start", "--foreground", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert result.output.startswith("Run backtest:bt-1\n")
    assert "  phase     stopped\n" in result.output
    assert "  events    2\n" in result.output


def test_run_daemon_start_format_option_overrides_workspace_default(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "[project]\nname = \"test\"\n[cli]\nformat = \"text\"\n",
        encoding="utf-8",
    )
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        app,
        [
            "run",
            "daemon",
            "start",
            "--foreground",
            "--root",
            str(root),
            "--mode",
            "backtest",
            "--config",
            str(config_path),
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["phase"] == "stopped"


def test_run_workspace_commands_explain_status_logs_artifacts_and_stop(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"
    RunDaemonService(root).run_foreground(mode="backtest", config_path=config_path)

    explain = CliRunner().invoke(run_app, ["explain", str(config_path), "--format", "json"], catch_exceptions=False)
    status = CliRunner().invoke(run_app, ["status", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    logs = CliRunner().invoke(run_app, ["logs", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    artifacts = CliRunner().invoke(run_app, ["artifacts", "bt-1", "--root", str(root), "--format", "json"], catch_exceptions=False)
    stop = CliRunner().invoke(run_app, ["stop", "bt-1", "--mode", "backtest", "--root", str(root)], catch_exceptions=False)

    assert explain.exit_code == 0
    assert json.loads(explain.output)["run_config"]["run"]["id"] == "bt-1"
    assert status.exit_code == 0
    assert json.loads(status.output)["runs"][0]["run_id"] == "bt-1"
    assert logs.exit_code == 0
    log_payload = json.loads(logs.output)
    assert log_payload["log_file"]
    assert log_payload["lines"]
    assert artifacts.exit_code == 0
    assert any(item["name"] == "summary.json" for item in json.loads(artifacts.output)["artifacts"])
    assert stop.exit_code == 0
    assert Path(json.loads(stop.output)["command_file"]).exists()


def test_run_daemon_start_background_command_launches_config(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    result = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "starting"
    assert payload["run_instance_id"]
    summary_path = root / "backtest" / "bt-1" / "summary.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not summary_path.exists():
        time.sleep(0.05)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["event_count"] == 2
    records = RunRegistry(root).list(mode="backtest", run_id="bt-1")
    assert len(records) == 1
    assert records[0].to_dict()["run_instance_id"] == payload["run_instance_id"]
    assert records[0].phase == "stopped"


def test_run_daemon_start_background_command_accepts_registered_target(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    register = CliRunner().invoke(run_app, ["register", "paper-printer", str(config_path)], catch_exceptions=False)
    result = CliRunner().invoke(
        run_app,
        ["daemon", "start", "paper-printer", "--root", str(root)],
        catch_exceptions=False,
    )

    assert register.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["phase"] == "starting"
    assert payload["run_id"] == "bt-1"
    summary_path = root / "backtest" / "bt-1" / "summary.json"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not summary_path.exists():
        time.sleep(0.05)
    assert json.loads(summary_path.read_text(encoding="utf-8"))["event_count"] == 2


def test_run_daemon_start_background_only_describes_target_before_spawning(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"
    resolver = _FakeBackgroundResolver()
    popen_calls = []

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            popen_calls.append((args, kwargs))

    monkeypatch.setattr("kairospy.application.system.control.daemon.subprocess.Popen", FakePopen)

    result = RunDaemonService(root, target_resolver=resolver).start_background(mode="backtest", config_path=config_path)

    assert result.phase == "starting"
    assert result.run_id == "described-run"
    assert resolver.described == [(RuntimeMode.BACKTEST, config_path)]
    assert resolver.resolved == []
    assert popen_calls
    assert popen_calls[0][1]["env"]["KAIROS_RUN_INSTANCE_ID"] == result.run_instance_id


def test_run_daemon_rejects_second_active_instance_for_same_run_id(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            pass

    monkeypatch.setattr("kairospy.application.system.control.daemon.subprocess.Popen", FakePopen)

    service = RunDaemonService(root)
    first = service.start_background(mode="backtest", config_path=config_path)

    with pytest.raises(RunAlreadyActiveError):
        service.start_background(mode="backtest", config_path=config_path)

    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == first.run_instance_id


def test_run_daemon_start_cli_reports_active_instance_conflict(tmp_path, monkeypatch) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"

    class FakePopen:
        pid = 12345

        def __init__(self, args, **kwargs) -> None:
            pass

    monkeypatch.setattr("kairospy.application.system.control.daemon.subprocess.Popen", FakePopen)

    first = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )
    second = CliRunner().invoke(
        run_app,
        ["daemon", "start", "--root", str(root), "--mode", "backtest", "--config", str(config_path)],
        catch_exceptions=False,
    )

    assert first.exit_code == 0
    assert second.exit_code != 0
    assert "run already has an active instance" in second.output


def test_run_daemon_allows_new_instance_after_stopped_and_keeps_artifacts_isolated(tmp_path) -> None:
    config_path = _write_backtest_project(tmp_path)
    root = tmp_path / "daemon-runs"
    service = RunDaemonService(root)

    first = service.run_foreground(mode="backtest", config_path=config_path)
    second = service.run_foreground(mode="backtest", config_path=config_path)

    assert first.run_instance_id != second.run_instance_id
    first_directory = root / "backtest" / "bt-1" / "instances" / str(first.run_instance_id)
    second_directory = root / "backtest" / "bt-1" / "instances" / str(second.run_instance_id)
    assert json.loads((first_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert json.loads((second_directory / "summary.json").read_text(encoding="utf-8"))["event_count"] == 2
    assert (first_directory / "account" / "current.json").exists()
    assert (second_directory / "account" / "current.json").exists()
    current = json.loads((root / "backtest" / "bt-1" / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == second.run_instance_id


def test_run_daemon_heartbeat_keeps_long_running_instance_active(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "run.toml"
    config_path.write_text("", encoding="utf-8")
    root = tmp_path / "daemon-runs"
    runner_can_finish = threading.Event()
    resolver = _FakeLongRunningResolver(runner_can_finish)
    monkeypatch.setattr("kairospy.application.system.control.daemon._STALE_AFTER_SECONDS", 0.25)
    monkeypatch.setattr("kairospy.application.system.control.daemon._HEARTBEAT_INTERVAL_SECONDS", 0.05)

    service = RunDaemonService(root, target_resolver=resolver)
    worker_error = []

    def run_worker() -> None:
        try:
            service.run_foreground(mode="backtest", config_path=config_path)
        except Exception as error:
            worker_error.append(error)

    worker = threading.Thread(target=run_worker)
    worker.start()
    state_path = root / "backtest" / "long-run" / "state.json"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if state.get("phase") == "running":
            break
        time.sleep(0.01)

    time.sleep(0.4)
    with pytest.raises(RunAlreadyActiveError):
        service.start_background(mode="backtest", config_path=config_path)

    runner_can_finish.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert worker_error == []


class _FakeBackgroundResolver:
    def __init__(self) -> None:
        self.described = []
        self.resolved = []

    def describe(self, mode: RuntimeMode, config_path: Path):
        self.described.append((mode, config_path))
        return _FakeDescriptor("described-run", "described/run/dir")

    def resolve(self, mode: RuntimeMode, config_path: Path):
        self.resolved.append((mode, config_path))
        raise AssertionError("background start must not resolve runtime target")


class _FakeDescriptor:
    def __init__(self, run_id: str, run_directory: str) -> None:
        self.run_id = run_id
        self.run_directory = run_directory


class _FakeLongRunningResolver:
    def __init__(self, runner_can_finish: threading.Event) -> None:
        self.runner_can_finish = runner_can_finish

    def describe(self, mode: RuntimeMode, config_path: Path):
        return _FakeDescriptor("long-run", "long/run/dir")

    def resolve(self, mode: RuntimeMode, config_path: Path):
        return _FakeLongRunningTarget(self.runner_can_finish)


class _FakeLongRunningTarget:
    def __init__(self, runner_can_finish: threading.Event) -> None:
        self.configured = _FakeLongRunningConfigured()
        self.runner_can_finish = runner_can_finish

    @property
    def run_id(self) -> str:
        return self.configured.run_id

    @property
    def run_directory(self) -> str:
        return str(self.configured.run_directory)

    def runner(self):
        self.runner_can_finish.wait(timeout=2)
        return _FakeRunResult()


class _FakeLongRunningConfigured:
    run_id = "long-run"
    run_directory = Path("long/run/dir")


class _FakeRunResult:
    runtime = None
    fills = ()
    trades = ()
    metrics = {}


def _write_backtest_project(root: Path) -> Path:
    (root / ".kairos").mkdir(parents=True, exist_ok=True)
    (root / ".kairos" / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    market_id = "market:binance:spot:btc_usdt"
    instrument_id = "instrument:spot:btc:usdt"
    (root / "strategy_mod.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.strategy import StrategyBase",
            "from kairospy.core.market import Bar",
            "from kairospy.core.intent import target_position_intent",
            "from kairospy.core.reference import MarketRef",
            "",
            "class ConfiguredStrategy(StrategyBase):",
            "    strategy_id = 'configured-strategy'",
            "    def __init__(self, instrument_id, market_id):",
            "        self.instrument_id = instrument_id",
            "        self.market_id = market_id",
            "        self.market_ref = MarketRef(",
            "            market_id=market_id,",
            "            instrument_id=instrument_id,",
            "            market_key='binance_spot_btc_usdt',",
            "            venue='binance',",
            "            market='spot',",
            "            source_symbol='BTC/USDT',",
            "        )",
            "        self.entered = False",
            "    def on_start(self, context):",
            "        context.subscribe(self.market_ref, selectors=(Bar.select(interval='1m'),), identity=self.strategy_id)",
            "    def on_data(self, context, signal):",
            "        if self.entered:",
            "            return None",
            "        self.entered = True",
            "        context.intent(target_position_intent(",
            "            strategy_id=self.strategy_id,",
            "            instrument_id=self.instrument_id,",
            "            market_id=self.market_id,",
            "            target_quantity=Decimal('2'),",
            "            at=signal.time,",
            "            intent_id='intent-1',",
            "        ))",
            "        return None",
        ]),
        encoding="utf-8",
    )
    DataStore(root / ".kairos" / "data", storage_format="jsonl").write(
        "market.ohlcv.binance.spot.btc_usdt.1m",
        (
            {
                "time": "2026-01-01T00:00:00+00:00",
                "kind": "bar",
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "market_id": market_id,
                "instrument_id": instrument_id,
                "market_key": "binance_spot_btc_usdt",
                "timeframe": "1m",
                "open": "101",
                "high": "101",
                "low": "101",
                "close": "101",
                "volume": "1",
            },
        ),
        mode="replace",
    )
    config_path = root / "run.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "bt-1"',
            'mode = "backtest"',
            'strategy = "strategy_mod:ConfiguredStrategy"',
            "",
            "[strategy.params]",
            f'instrument_id = "{instrument_id}"',
            f'market_id = "{market_id}"',
            "",
            "[account]",
            "cash = 1000",
            'currency = "USDT"',
            "",
            "[backtest]",
            'runs_root = "runs"',
            'storage_format = "jsonl"',
            'price_field = "close"',
            "",
            "[backtest.market]",
            'start = "2026-01-01T00:00:00+00:00"',
            'end = "2026-01-01T00:01:00+00:00"',
            'on_missing = "error"',
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path
