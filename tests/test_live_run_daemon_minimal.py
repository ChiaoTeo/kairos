from __future__ import annotations

import json
import threading
import time
from io import StringIO
from decimal import Decimal
from pathlib import Path

from kairospy.core.account import AccountContext, AccountRef, Environment
from kairospy.application.context import DataContext
from kairospy.infrastructure.data import DataStore
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.application.mode.live import LiveEngine, LiveEngineDaemonTarget
from kairospy.application.service.domains.market import IterableEventSource
from kairospy.application.service.operations.run import RunAccountJournal
from kairospy.application.runtime.control import RunDaemonControlPlane, RunDaemonPhase
from kairospy.surface import cli
from kairospy.surface.products import run as run_product
from kairospy.surface.products.run import OutputFormat, RunAttachSession
from kairospy.application.strategy import StrategyBase


class DaemonAccountReadingStrategy(StrategyBase):
    strategy_id = "daemon-live-account-reader"


class DaemonFakeGateway:
    def fetch_balance(self, *, params=None):
        return {
            "free": {"USDT": "1000"},
            "used": {"USDT": "0"},
            "total": {"USDT": "1000"},
        }

    def fetch_open_orders(self, symbol=None, *, params=None):
        return []

    def watch_balance(self, *, params=None):
        return _empty_async()

    def watch_orders(self, symbol=None, *, params=None):
        return _empty_async()

    def watch_my_trades(self, symbol=None, *, params=None):
        return _empty_async()


def test_live_run_foreground_publishes_heartbeats_and_stops_after_duration(tmp_path) -> None:
    control = RunDaemonControlPlane("duration-run", root=tmp_path)

    status = control.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    assert status.phase is RunDaemonPhase.STOPPED
    assert status.reason == "duration elapsed"
    stored = control.status()
    assert stored.phase is RunDaemonPhase.STOPPED
    assert stored.heartbeat_at is not None


def test_run_daemon_control_plane_supports_non_live_modes(tmp_path) -> None:
    control = RunDaemonControlPlane("paper-run", mode="paper", root=tmp_path)

    status = control.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    assert status.phase is RunDaemonPhase.STOPPED
    assert status.mode.value == "paper"
    assert status.to_dict()["mode"] == "paper"
    assert (tmp_path / "paper" / "paper-run" / "state.json").exists()


def test_run_daemon_instances_are_grouped_under_logical_run_id(tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    first = RunDaemonControlPlane("paper-run", mode="paper", root=runtime_root, instance_id="first")
    second = RunDaemonControlPlane("paper-run", mode="paper", root=runtime_root, instance_id="second")

    first.run_foreground(poll_seconds=0.01, duration_seconds=0.02)
    second.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    assert (runtime_root / "paper" / "paper-run" / "runs" / "first" / "state.json").exists()
    assert (runtime_root / "paper" / "paper-run" / "runs" / "second" / "state.json").exists()
    current = json.loads((runtime_root / "paper" / "paper-run" / "current.json").read_text(encoding="utf-8"))
    assert current["run_id"] == "paper-run"
    assert current["run_instance_id"] == "second"
    latest = RunDaemonControlPlane("paper-run", mode="paper", root=runtime_root)
    assert latest.directory == second.directory
    assert latest.status().run_instance_id == "second"
    history = run_product.list_run_instances("paper-run", mode="paper", root=runtime_root)
    assert [status.run_instance_id for status in history] == ["first", "second"]


def test_live_daemon_target_uses_generic_runtime_daemon_boundary() -> None:
    text = Path("kairospy/application/mode/live/daemon.py").read_text(encoding="utf-8")

    assert "RunDaemonPhase" in text
    assert "RunExecutionContext" in text
    assert "LiveRunDaemonPhase" not in text
    assert "LiveRunExecutionContext" not in text


def test_runtime_daemon_targets_are_owned_symmetrically_by_modes() -> None:
    for mode in ("backtest", "paper", "live"):
        assert Path("kairospy", "application", "mode", mode, "daemon.py").exists()


def test_live_run_stop_request_stops_foreground_daemon(tmp_path) -> None:
    control = RunDaemonControlPlane("operator-stop", root=tmp_path)
    result = {}

    thread = threading.Thread(
        target=lambda: result.setdefault(
            "status",
            control.run_foreground(poll_seconds=0.01),
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and control.status().phase is not RunDaemonPhase.RUNNING:
        time.sleep(0.01)

    command = control.request_stop(reason="test stop", actor="pytest")
    thread.join(timeout=1.0)

    assert command["reason"] == "test stop"
    assert not thread.is_alive()
    assert result["status"].phase is RunDaemonPhase.STOPPED
    assert result["status"].reason == "test stop"


def test_cli_run_daemon_live_status_outputs_json() -> None:
    stdout = StringIO()

    result = cli.execute_argv(["run", "daemon", "status", "--mode", "live", "--run-id", "cli-smoke"], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "cli-smoke"
    assert payload["mode"] == "live"
    assert payload["phase"] in {"created", "stopped", "running", "stale"}


def test_cli_run_daemon_status_outputs_mode_json() -> None:
    stdout = StringIO()

    result = cli.execute_argv(["run", "daemon", "status", "--mode", "paper", "--run-id", "paper-smoke"], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "paper-smoke"
    assert payload["mode"] == "paper"
    assert payload["phase"] in {"created", "stopped", "running", "stale"}


def test_cli_run_daemon_start_can_run_configured_live_target(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "okx_live.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "okx-live-cli"',
            'mode = "live"',
            'strategy = "strategies:NoopStrategy"',
            "",
            "[live]",
            'venue = "okx"',
            'market = "spot"',
            'symbol = "BTC/USDT"',
            "",
            "[accounts.okx_main]",
            "index = 0",
            'venue = "okx"',
            'credential = "env:okx-main"',
        ]),
        encoding="utf-8",
    )

    class FakeLiveTarget:
        def run(self, context):
            context.heartbeat(metrics={"mode_run_status": "running", "venue": "okx"})
            return {"run_id": context.run_id, "venue": "okx", "submitted": False}

    def fake_configured_live_target(path, *, exchange_factory):
        assert path == config_path
        return FakeLiveTarget()

    monkeypatch.setattr(run_product, "_runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(run_product, "configured_live_target", fake_configured_live_target)
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "daemon",
        "start",
        "--mode",
        "live",
        "--config",
        str(config_path),
        "--foreground",
        "--poll-seconds",
        "0.01",
    ], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "okx-live-cli"
    assert payload["mode"] == "live"
    assert payload["phase"] == "stopped"
    assert payload["result"] == {"run_id": "okx-live-cli", "submitted": False, "venue": "okx"}


def test_cli_run_daemon_status_can_output_text() -> None:
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "daemon",
        "status",
        "--mode",
        "paper",
        "--run-id",
        "paper-smoke",
        "--format",
        "text",
    ], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "paper run paper-smoke:" in text
    assert "phase" in text
    assert "heartbeat" in text
    assert not text.lstrip().startswith("{")


def test_run_daemon_context_and_events_are_persisted(tmp_path) -> None:
    control = RunDaemonControlPlane("context-run", mode="paper", root=tmp_path)

    control.update_context({"config_file": "examples/paper.toml", "strategy": "demo:Strategy"})
    status = control.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    assert status.context["config_file"] == "examples/paper.toml"
    assert status.to_dict()["context"]["strategy"] == "demo:Strategy"
    assert (tmp_path / "paper" / "context-run" / "context.json").exists()
    events = (tmp_path / "paper" / "context-run" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["type"] == "context" for line in events)
    assert any(json.loads(line)["type"] == "status" for line in events)


def test_cli_run_shell_can_track_selected_run() -> None:
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "shell",
        "--command",
        "use paper shell-smoke",
        "--command",
        "status",
    ], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Using paper run shell-smoke" in text
    assert "paper run shell-smoke:" in text


def test_cli_run_list_outputs_recorded_runs(tmp_path) -> None:
    control = RunDaemonControlPlane("listed-paper", mode="paper", root=tmp_path / "paper")
    control.update_context({"strategy": "demo:Strategy"})
    control.run_foreground(poll_seconds=0.01, duration_seconds=0.02)

    rows = run_product.list_run_daemons(mode="paper", root=tmp_path)

    assert len(rows) == 1
    assert rows[0].run_id == "listed-paper"
    assert rows[0].context["strategy"] == "demo:Strategy"


def test_cli_run_shell_can_list_and_select_run() -> None:
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "shell",
        "--command",
        "list --mode paper",
        "--command",
        "use 1",
        "--command",
        "status",
    ], stdout)

    assert result == 0
    text = stdout.getvalue()
    assert "Runs" in text
    assert "Using paper run" in text
    assert "paper run" in text


def test_run_attach_session_can_show_context_tail_and_stop(tmp_path) -> None:
    control = RunDaemonControlPlane("attach-run", mode="paper", root=tmp_path)
    control.update_context({"config_file": "examples/paper.toml", "strategy": "demo:Strategy"})
    control.log_path.write_text("first\nsecond\n", encoding="utf-8")
    stdout = StringIO()
    session = RunAttachSession(
        control,
        stale_after_seconds=5,
        poll_seconds=0.01,
        output_format=OutputFormat.text,
        tail_lines=1,
        stdout=stdout,
    )

    assert not session.handle("context")
    assert not session.handle("tail 1")
    assert not session.handle("stop maintenance")
    assert session.handle("quit")

    text = stdout.getvalue()
    assert "Context" in text
    assert "examples/paper.toml" in text
    assert "second" in text
    assert "maintenance" in text
    assert json.loads(control.command_path.read_text(encoding="utf-8"))["reason"] == "maintenance"


def test_run_attach_session_can_show_account_journal(tmp_path) -> None:
    control = RunDaemonControlPlane("attach-account", mode="paper", root=tmp_path)
    journal = RunAccountJournal(control.directory)
    journal.write_current({"run_id": "attach-account", "mode": "paper", "net_profit": "12"})
    journal.replace_jsonl(
        journal.positions_path,
        [{"instrument_id": "instrument:spot:btc:usdc", "quantity": "2"}],
    )
    journal.replace_jsonl(
        journal.equity_path,
        [{"time": "2027-01-01T00:00:00+00:00", "equity": "1012", "net_profit": "12"}],
    )
    stdout = StringIO()
    session = RunAttachSession(
        control,
        stale_after_seconds=5,
        poll_seconds=0.01,
        output_format=OutputFormat.text,
        tail_lines=1,
        stdout=stdout,
    )

    assert not session.handle("summary")
    assert not session.handle("positions")
    assert not session.handle("pnl --limit 1")

    text = stdout.getvalue()
    assert "Account Summary" in text
    assert "net profit" in text
    assert "instrument:spot:btc:usdc" in text
    assert "1012" in text


def test_cli_run_daemon_start_can_run_configured_backtest(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "kairos.toml").write_text("schema_version = 1\n", encoding="utf-8")
    (project / "strategies.py").write_text(
        "\n".join([
            "from kairospy.application.strategy import StrategyBase",
            "",
            "class NoopStrategy(StrategyBase):",
            '    strategy_id = "configured-daemon-noop"',
        ]),
        encoding="utf-8",
    )
    events = project / "events.jsonl"
    events.write_text(
        json.dumps({
            "time": "2026-01-01T00:00:00+00:00",
            "kind": "quote",
            "market_id": "market:simulated:spot:btc_usdt",
            "instrument_id": "instrument:spot:btc:usdt",
            "market_key": "simulated_spot_btc_usdt",
            "bid": "100",
            "ask": "101",
        }) + "\n",
        encoding="utf-8",
    )
    config_path = project / "run.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "configured-daemon-backtest"',
            'mode = "backtest"',
            'strategy = "strategies:NoopStrategy"',
            "",
            "[account]",
            'cash = "1000"',
            'currency = "USDT"',
            'environment = "backtest"',
            "",
            "[backtest]",
            'events = "events.jsonl"',
            'stream = "simulated.quote.BTC/USDT"',
        ]),
        encoding="utf-8",
    )
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "daemon",
        "start",
        "--mode",
        "backtest",
        "--run-id",
        "configured-cli-backtest",
        "--config",
        str(config_path),
        "--foreground",
        "--poll-seconds",
        "0.01",
    ], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "configured-cli-backtest"
    assert payload["mode"] == "backtest"
    assert payload["phase"] == "stopped"
    assert payload["result"]["run_id"] == "configured-daemon-backtest"
    assert payload["result"]["strategy_id"] == "configured-daemon-noop"
    assert payload["result"]["mode"] == "backtest"
    assert payload["result"]["event_count"] == 1


def test_configured_daemon_start_keeps_run_id_stable_and_generates_instance_ids(tmp_path) -> None:
    config_path = tmp_path / "paper.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "configured-paper"',
            'mode = "paper"',
        ]),
        encoding="utf-8",
    )

    run_id = run_product._resolve_daemon_run_id("start", run_id=None, config_path=config_path)
    first = run_product._unique_run_instance_id()
    second = run_product._unique_run_instance_id()

    assert run_id == "configured-paper"
    assert first[:8].isdigit()
    assert first[8] == "T"
    assert second[:8].isdigit()
    assert second[8] == "T"
    assert first != second


def test_cli_run_daemon_start_can_run_configured_streaming_paper(monkeypatch, tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "kairos.toml").write_text("schema_version = 1\n", encoding="utf-8")
    (project / "paper_strategies.py").write_text(
        "\n".join([
            "from decimal import Decimal",
            "from kairospy.application.context import StrategyContext",
            "from kairospy.core.market import Quote",
            "from kairospy.application.strategy import StrategyBase, StrategySignal",
            "",
            "class PaperStrategy(StrategyBase):",
            '    strategy_id = "configured-streaming-paper"',
            "    def __init__(self):",
            "        self.entered = False",
            "    def on_start(self, context: StrategyContext):",
            '        context.subscribe_market_data("binance:spot:BTC/USDC", selectors=(Quote.select("bid", "ask", basis="ticker"),))',
            "        return ()",
            "    def on_market(self, context: StrategyContext, signal: StrategySignal):",
            "        if self.entered:",
            "            return ()",
            '        context.target_position("binance:spot:BTC/USDC", Decimal("1"), account=0, intent_id="enter")',
            "        self.entered = True",
            "        return ()",
        ]),
        encoding="utf-8",
    )
    config_path = project / "paper.toml"
    config_path.write_text(
        "\n".join([
            "[run]",
            'id = "configured-daemon-paper"',
            'mode = "paper"',
            'strategy = "paper_strategies:PaperStrategy"',
            "",
            "[accounts.ops_main]",
            "index = 0",
            'venue = "binance"',
            'cash = "1000"',
            'currency = "USDC"',
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(run_product, "exchange", lambda exchange_name, driver_name: DaemonFakePaperExchange())
    stdout = StringIO()

    result = cli.execute_argv([
        "run",
        "daemon",
        "start",
        "--mode",
        "paper",
        "--run-id",
        "configured-cli-paper",
        "--config",
        str(config_path),
        "--foreground",
        "--poll-seconds",
        "0.01",
    ], stdout)

    assert result == 0
    payload = json.loads(stdout.getvalue())
    assert payload["run_id"] == "configured-cli-paper"
    assert payload["mode"] == "paper"
    assert payload["phase"] == "stopped"
    assert payload["result"]["run_id"] == "configured-daemon-paper"
    assert payload["result"]["strategy_id"] == "configured-streaming-paper"
    assert payload["result"]["mode"] == "paper"
    assert payload["result"]["event_count"] == 1
    assert payload["result"]["fills"] == 1
    journal = RunAccountJournal(RunDaemonControlPlane("configured-cli-paper", mode="paper").directory)
    assert journal.read_current()["net_profit"] == "0"
    assert journal.read_rows("fills")[0]["instrument_id"] == "instrument:spot:btc:usdc"
    assert journal.read_rows("positions")[0]["quantity"] == "1"

    positions_stdout = StringIO()
    assert cli.execute_argv([
        "run",
        "account",
        "positions",
        "--mode",
        "paper",
        "--run-id",
        "configured-cli-paper",
        "--format",
        "text",
    ], positions_stdout) == 0
    assert "instrument:spot:btc:usdc" in positions_stdout.getvalue()

    shell_stdout = StringIO()
    assert cli.execute_argv([
        "run",
        "shell",
        "--command",
        "use paper configured-cli-paper",
        "--command",
        "pnl",
    ], shell_stdout) == 0
    assert "Account Pnl" in shell_stdout.getvalue()
    assert "1000" in shell_stdout.getvalue()


def test_live_engine_daemon_target_runs_loop_and_persists_iteration_status(tmp_path) -> None:
    control = RunDaemonControlPlane("target-run", root=tmp_path)
    account = AccountContext(AccountRef("binance", "main", "spot"), Environment.LIVE)
    engine = LiveEngine(
        DaemonAccountReadingStrategy(),
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        account,
        DaemonFakeGateway(),
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency="USDT",
    )
    target = LiveEngineDaemonTarget(
        engine,
        lambda iteration: IterableEventSource(
            "binance.quote.BTC/USDT",
            [
                {
                    "time": f"2026-01-01T00:0{iteration}:00+00:00",
                    "kind": "quote",
                    "market_id": "market:binance:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "binance_spot_btc_usdt",
                    "bid": Decimal("100"),
                    "ask": Decimal("101"),
                }
            ],
        ),
        symbol="BTC/USDT",
        max_iterations=1,
        retry_backoff_seconds=0,
    )

    status = control.run_foreground(poll_seconds=0.01, target=target)

    assert status.phase is RunDaemonPhase.STOPPED
    assert status.reason == "target completed"
    assert status.result["iterations"] == 1
    assert status.result["succeeded_count"] == 1
    assert status.result["latest_strategy_id"] == "daemon-live-account-reader"
    assert control.status().metrics["iteration"] == 1
    journal = RunAccountJournal(control.directory)
    current = journal.read_current()
    assert current["equity"] == "1000"
    assert current["account_view"]["balances"][0]["currency"] == "USDT"


async def _empty_async():
    if False:
        yield None


class DaemonFakePaperExchange:
    async def watch_ticker(self, symbol, *, params=None):
        assert symbol == "BTC/USDC"
        yield {
            "time": "2027-01-01T00:00:00+00:00",
            "kind": "ticker",
            "market_id": "market:binance:spot:btc_usdc",
            "instrument_id": "instrument:spot:btc:usdc",
            "market_key": "binance_spot_btc_usdc",
            "venue": "binance",
            "market": "spot",
            "source_symbol": symbol,
            "bid1": "100",
            "ask1": "101",
        }
