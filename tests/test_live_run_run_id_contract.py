from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = (
    ROOT / "kairospy",
    ROOT / "docs",
    ROOT / "tests",
)


def test_live_run_uses_run_id_as_the_only_run_identity_name() -> None:
    forbidden = "live_" + "run_id"
    violations: list[str] = []
    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".md"}:
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if forbidden in text:
                violations.append(str(path.relative_to(ROOT)))
    assert violations == [], "live run must use run_id only:\n" + "\n".join(violations)


def test_run_live_cli_requires_explicit_run_id() -> None:
    from kairospy.surface.cli.main import _parser

    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "live", "status"])

    parsed = parser.parse_args(["run", "live", "status", "--run-id", "venue-a-live"])
    configured = parser.parse_args([
        "run",
        "live",
        "start",
        "--run-id",
        "venue-a-live",
        "--config",
        "configs/runs/live.toml",
        "--param",
        "risk=low",
        "--confirm-live",
    ])
    foreground = parser.parse_args([
        "run",
        "live",
        "start",
        "--run-id",
        "venue-a-live",
        "--config",
        "configs/runs/live.toml",
        "--confirm-live",
        "--foreground",
    ])
    kill = parser.parse_args([
        "run",
        "live",
        "kill-switch",
        "--run-id",
        "venue-a-live",
        "--actor",
        "alice",
        "--reason",
        "risk breach",
    ])
    reload_risk = parser.parse_args([
        "run",
        "live",
        "reload-risk-limits",
        "--run-id",
        "venue-a-live",
        "--risk-limits-hash",
        "limits-hash",
    ])
    run_status = parser.parse_args(["run", "status", "--run-id", "venue-a-live", "--fresh", "--wait", "1"])
    run_stop = parser.parse_args([
        "run",
        "stop",
        "--run-id",
        "venue-a-live",
        "--actor",
        "alice",
        "--reason",
        "maintenance",
    ])
    run_pause = parser.parse_args(["run", "pause", "--run-id", "venue-a-live", "--reason", "feed stale"])
    run_reduce = parser.parse_args(["run", "reduce-only", "--run-id", "venue-a-live", "--reason", "risk review"])
    run_commands = parser.parse_args(["run", "commands", "--run-id", "venue-a-live", "--limit", "5"])
    live_pause = parser.parse_args(["run", "live", "pause", "--run-id", "venue-a-live", "--reason", "feed stale"])
    live_attach = parser.parse_args(["run", "live", "attach", "--run-id", "venue-a-live", "--no-follow"])
    workspace_list = parser.parse_args(["workspace", "list"])

    assert parsed.group == "run"
    assert parsed.action == "live"
    assert parsed.live_action == "status"
    assert parsed.run_id == "venue-a-live"
    assert configured.config == Path("configs/runs/live.toml")
    assert configured.param == ["risk=low"]
    assert configured.confirm_live is True
    assert configured.foreground is False
    assert foreground.foreground is True
    assert kill.live_action == "kill-switch"
    assert kill.actor == "alice"
    assert kill.reason == "risk breach"
    assert reload_risk.live_action == "reload-risk-limits"
    assert reload_risk.risk_limits_hash == "limits-hash"
    assert run_status.action == "status"
    assert run_status.run_id == "venue-a-live"
    assert run_status.fresh is True
    assert run_status.wait == 1
    assert run_stop.action == "stop"
    assert run_stop.actor == "alice"
    assert run_pause.action == "pause"
    assert run_reduce.action == "reduce-only"
    assert run_commands.action == "commands"
    assert run_commands.limit == 5
    assert live_pause.live_action == "pause"
    assert live_attach.live_action == "attach"
    assert live_attach.no_follow is True
    assert workspace_list.group == "workspace"
    assert workspace_list.action == "list"
    assert "live_" + "run_id" not in vars(parsed)


def test_run_live_attach_log_tail_ignores_missing_or_directory_log_path(tmp_path) -> None:
    from kairospy.surface.cli import main as cli_main

    assert cli_main._run_live_attach_print_tail(None, 20) == 0
    assert cli_main._run_live_attach_print_new_log(None, 10) == 10
    assert cli_main._run_live_attach_print_tail(tmp_path, 20) == 0
    assert cli_main._run_live_attach_print_new_log(tmp_path, 10) == 10


def test_run_live_attach_repl_helpers_support_prompt_and_full_commands() -> None:
    from kairospy.surface.cli import main as cli_main

    attach_args = SimpleNamespace(
        run_id="venue-a-live",
        config=None,
        param=[],
        confirm_live=False,
        poll_seconds=0.25,
        log_file=None,
    )
    payload = {
        "status": "running",
        "phase": "running",
        "reason": "started",
        "stop_requested": False,
        "metrics": {"operator_command_backlog": 0, "open_incident_count": 0},
    }

    assert cli_main._run_live_attach_prompt("venue-a-live") == "kairos[venue-a-live]> "
    assert cli_main._run_live_attach_status_key(payload) == ("running", "running", "started", False, 0, 0)
    assert cli_main._run_live_attach_normalize_command_parts(["run", "live", "status"]) == ["status"]
    assert cli_main._run_live_attach_normalize_command_parts(["kairospy", "run", "live", "stop"]) == ["stop"]
    assert cli_main._run_live_attach_reason("stop", ["--run-id", "venue-a-live", "--reason", "manual stop"]) == "manual stop"
    assert cli_main._run_live_attach_reason("reduce-only", ["risk", "review"]) == "risk review"
    start_args = cli_main._run_live_attach_start_args(
        "start",
        attach_args,
        ["--config", "configs/runs/live.toml", "--param", "risk=low", "--confirm-live"],
    )
    assert start_args.live_action == "start"
    assert start_args.run_id == "venue-a-live"
    assert start_args.config == Path("configs/runs/live.toml")
    assert start_args.param == ["risk=low"]
    assert start_args.confirm_live is True
    assert start_args.foreground is False
    assert start_args.duration_seconds is None


def test_top_level_interactive_shell_routes_live_shortcut(monkeypatch) -> None:
    from kairospy.surface.cli import main as cli_main

    calls: list[list[str]] = []
    monkeypatch.setattr(cli_main, "main", lambda argv: calls.append(list(argv)) or 0)
    session = cli_main._InteractiveSession()

    should_exit = session.handle("live venue-a-live --no-follow")

    assert should_exit is False
    assert calls == [["run", "live", "attach", "--run-id", "venue-a-live", "--no-follow"]]


def test_interactive_shell_state_machine_routes_modes_and_number_shortcuts(monkeypatch) -> None:
    from kairospy.surface.cli import main as cli_main

    calls: list[list[str]] = []
    monkeypatch.setattr(cli_main, "main", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setattr(cli_main, "_interactive_workspace_choices", lambda: ("alpha", "beta"))
    session = cli_main._InteractiveSession()

    assert session.prompt() == "kairos> "
    assert session.handle("1") is False
    assert session.mode == "run"
    assert session.prompt() == "kairos/run> "
    assert session.handle("live venue-a-live --no-follow") is False
    assert calls[-1] == ["run", "live", "attach", "--run-id", "venue-a-live", "--no-follow"]
    assert session.handle("back") is False
    assert session.mode == "top"
    assert session.handle("2") is False
    assert session.mode == "workspace"
    assert calls[-1] == ["workspace", "list"]
    assert session.handle("1") is False
    assert session.workspace_name == "alpha"
    assert session.prompt() == "kairos/workspace[alpha]> "
    assert session.handle("1") is False
    assert calls[-1] == ["workspace", "inspect", "alpha"]
    assert session.handle("2 --name bars --dataset local.market_ticks") is False
    assert calls[-1] == ["workspace", "attach", "alpha", "--name", "bars", "--dataset", "local.market_ticks"]
    assert session.handle("back") is False
    assert session.workspace_name is None
    assert session.mode == "workspace"
    assert session.handle("0 gamma") is False
    assert calls[-1] == ["workspace", "create", "gamma"]
    assert session.workspace_name == "gamma"
    assert session.handle("back") is False
    assert session.handle("back") is False
    assert session.handle("3") is False
    assert session.mode == "data"
    assert session.handle("1") is False
    assert calls[-1] == ["data", "list"]
    assert session.handle("back") is False
    assert session.handle("4") is False
    assert session.mode == "config"
    assert session.handle("1") is False
    assert calls[-1] == ["config", "show"]


def test_workspace_list_control_surface(tmp_path, monkeypatch, capsys) -> None:
    from kairospy import Workspace, initialize_project
    from kairospy.surface.cli import main as cli_main

    initialize_project(tmp_path, name="Workspace List Contract")
    monkeypatch.chdir(tmp_path)
    Workspace.open_or_create("alpha", start=tmp_path)

    assert cli_main.main(["--format", "json", "workspace", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "list"
    assert payload["workspace_count"] == 1
    assert payload["workspaces"][0]["name"] == "alpha"


def test_run_live_product_surface_rejects_missing_or_blank_run_id(tmp_path, monkeypatch) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="--run-id"):
        product_surface.run_live(SimpleNamespace(live_action="status"))
    with pytest.raises(ValueError, match="--run-id"):
        product_surface.run_live(SimpleNamespace(live_action="status", run_id="  "))


@pytest.mark.parametrize("run_id", ("../escape", "venue/live", "venue\\live", ".", "..", " venue-a-live "))
def test_run_live_product_surface_rejects_path_like_run_id(
    run_id: str,
    tmp_path,
    monkeypatch,
) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="--run-id"):
        product_surface.run_live(SimpleNamespace(live_action="status", run_id=run_id))


@pytest.mark.parametrize("action", ("start", "recover"))
def test_run_live_start_and_recover_require_confirm_before_service_binding(
    action: str,
    tmp_path,
    monkeypatch,
) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text(
        '[project]\nname = "live-run-contract"\n\n[execution]\nlive_trading_enabled = true\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="--confirm-live"):
        product_surface.run_live(SimpleNamespace(live_action=action, run_id="venue-a-live"))


@pytest.mark.parametrize("action", ("start", "recover"))
def test_run_live_start_and_recover_require_live_trading_enabled(
    action: str,
    tmp_path,
    monkeypatch,
) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text(
        '[project]\nname = "live-run-contract"\n\n[execution]\nlive_trading_enabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="live_trading_enabled"):
        product_surface.run_live(SimpleNamespace(
            live_action=action,
            run_id="venue-a-live",
            confirm_live=True,
        ))


def test_manual_target_position_live_run_config_does_not_require_live_trading_unlock(tmp_path) -> None:
    from kairospy.runtime.run_config import load_run_config
    from kairospy.surface import product as product_surface

    path = tmp_path / "manual-target-position.toml"
    path.write_text(
        "\n".join([
            "schema_version = 1",
            "",
            "[run]",
            'name = "manual-target-position"',
            'mode = "live"',
            "",
            "[bindings]",
            'account = "binance_live_spot"',
            "",
            "[live]",
            'provider = "binance"',
            'execution_driver = "manual-target-position"',
            "bind_ports = false",
            "",
            "[evidence]",
            'readiness = "readiness:manual"',
            'promotion = "promotion:manual"',
        ]) + "\n",
        encoding="utf-8",
    )

    assert product_surface._run_config_requires_live_trading_enabled(load_run_config(path)) is False


def test_live_execution_run_config_still_requires_live_trading_unlock(tmp_path) -> None:
    from kairospy.runtime.run_config import load_run_config
    from kairospy.surface import product as product_surface

    path = tmp_path / "binance-live.toml"
    path.write_text(
        "\n".join([
            "schema_version = 1",
            "",
            "[run]",
            'name = "binance-live"',
            'mode = "live"',
            "",
            "[bindings]",
            'account = "binance_live_spot"',
            "",
            "[live]",
            'provider = "binance"',
            'execution_driver = "binance-live"',
            "bind_ports = true",
            "",
            "[evidence]",
            'readiness = "readiness:live"',
            'promotion = "promotion:live"',
        ]) + "\n",
        encoding="utf-8",
    )

    assert product_surface._run_config_requires_live_trading_enabled(load_run_config(path)) is True


def test_run_live_status_uses_run_id_scoped_runtime_state(tmp_path, monkeypatch) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    first = product_surface.run_live(SimpleNamespace(live_action="status", run_id="venue-a-live"))
    second = product_surface.run_live(SimpleNamespace(live_action="status", run_id="venue-b-live"))

    assert first["run_id"] == "venue-a-live"
    assert second["run_id"] == "venue-b-live"
    assert first["status"] == "not_started"
    assert second["status"] == "not_started"
    assert first["health"]["status"] == "inactive"
    assert first["health"]["healthy"] is False
    assert first["metrics"]["health_status"] == "inactive"
    assert first["state_key"] == "live_run_daemon:venue-a-live"
    assert second["state_key"] == "live_run_daemon:venue-b-live"
    assert first["runtime_database"] != second["runtime_database"]
    assert "/runtime/live/venue-a-live/" in first["runtime_database"]
    assert "/runtime/live/venue-b-live/" in second["runtime_database"]


def test_run_live_status_does_not_create_runtime_database_for_missing_run(tmp_path, monkeypatch) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    status = product_surface.run_live(SimpleNamespace(live_action="status", run_id="venue-a-live"))

    assert status["status"] == "not_started"
    assert status["state_key"] == "live_run_daemon:venue-a-live"
    assert not Path(status["runtime_database"]).exists()


def test_run_live_stop_request_is_scoped_to_one_run_id(tmp_path, monkeypatch) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    stopped = product_surface.run_live(SimpleNamespace(
        live_action="stop",
        run_id="venue-a-live",
        reason="operator maintenance",
    ))
    first = product_surface.run_live(SimpleNamespace(live_action="status", run_id="venue-a-live"))
    second = product_surface.run_live(SimpleNamespace(live_action="status", run_id="venue-b-live"))

    assert stopped["run_id"] == "venue-a-live"
    assert stopped["status"] == "stop_requested"
    assert first["stop_requested"] is True
    assert first["reason"] == "operator maintenance"
    assert first["state_key"] == "live_run_daemon:venue-a-live"
    assert second["run_id"] == "venue-b-live"
    assert second["status"] == "not_started"
    assert second["state_key"] == "live_run_daemon:venue-b-live"
    assert "stop_requested" not in second
    assert first["runtime_database"] != second["runtime_database"]


def test_run_live_reset_kill_switch_requires_reconciliation_evidence(tmp_path, monkeypatch) -> None:
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="reconciliation-evidence"):
        product_surface.run_live(SimpleNamespace(
            live_action="reset-kill-switch",
            run_id="venue-a-live",
            actor="alice",
            reason="reconciled",
        ))


def test_run_live_status_prefers_stale_heartbeat_over_last_running_phase(tmp_path, monkeypatch) -> None:
    from kairospy.infrastructure.configuration import PROJECT_STATE_DIR
    from kairospy.runtime import LiveRunDaemon, LiveRunProcessIdentity, LiveRunRegistry
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_id = "venue-a-live"
    runtime_root = tmp_path / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    runtime_db = RuntimePaths.under(runtime_root).runtime_database
    store = SQLiteRuntimeStore(runtime_db)
    at = datetime.now(timezone.utc) - timedelta(seconds=10)
    store.set_runtime_state(
        f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}",
        {
            "run_id": run_id,
            "phase": "running",
            "application_status": "running",
            "reason": "started",
            "services": [],
            "stop_requested": False,
            "snapshot_hash": "hash",
        },
        at,
    )
    LiveRunRegistry(store).heartbeat(
        LiveRunProcessIdentity.create(run_id=run_id, runtime_id=run_id, started_at=at),
        observed_state="running",
        desired_state="running",
        state={"phase": "running"},
        at=at,
    )

    status = product_surface.run_live(SimpleNamespace(
        live_action="status",
        run_id=run_id,
        stale_after_seconds=1.0,
    ))

    assert status["phase"] == "running"
    assert status["status"] == "stale"
    assert status["heartbeat"]["stale"] is True
    assert status["heartbeat"]["pid"]
    assert status["heartbeat"]["host"]
    assert status["health"]["status"] == "stale"
    assert status["health"]["healthy"] is False
    assert "heartbeat_stale" in status["health"]["reasons"]


def test_run_live_status_includes_recovery_state_for_unresolved_orders(tmp_path, monkeypatch) -> None:
    from decimal import Decimal

    from kairospy.execution.events import TradeSide
    from kairospy.execution.order_state import DurableOrderStatus
    from kairospy.execution.orders import ExecutionInstructions, OrderType, TimeInForce
    from kairospy.infrastructure.configuration import PROJECT_STATE_DIR
    from kairospy.integrations.ports import OrderRequest
    from kairospy.identity import AccountRef, AccountType, InstitutionId, InstrumentId
    from kairospy.runtime import LiveRunDaemon
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_id = "venue-a-live"
    runtime_root = tmp_path / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    store = SQLiteRuntimeStore(RuntimePaths.under(runtime_root).runtime_database)
    at = datetime.now(timezone.utc)
    store.set_runtime_state(
        f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}",
        {
            "run_id": run_id,
            "phase": "running",
            "application_status": "running",
            "reason": "started",
            "services": [],
            "stop_requested": False,
        },
        at,
    )
    store.create_order(
        OrderRequest(
            "internal-1",
            "client-recovery-1",
            "strategy-v1",
            "intent-1",
            "correlation-1",
            AccountRef(InstitutionId("binance"), "spot-main", AccountType.CRYPTO_SPOT),
            InstrumentId("BTC-USDT"),
            TradeSide.BUY,
            Decimal("0.1"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("50000")),
        ),
        at,
    )
    store.transition_order("client-recovery-1", DurableOrderStatus.APPROVED, at)
    store.transition_order("client-recovery-1", DurableOrderStatus.SUBMITTING, at)

    status = product_surface.run_live(SimpleNamespace(
        live_action="status",
        run_id=run_id,
        stale_after_seconds=5.0,
    ))

    assert status["status"] == "running"
    assert status["recovery_state"]["unresolved_order_count"] == 1
    assert status["recovery_state"]["orders_requiring_recovery_count"] == 1
    assert status["recovery_state"]["unresolved_client_order_ids"] == ("client-recovery-1",)
    assert status["recovery_state"]["orders_requiring_recovery_client_order_ids"] == ("client-recovery-1",)
    assert status["health"]["status"] == "blocking"
    assert status["health"]["healthy"] is False
    assert "unresolved_orders" in status["health"]["reasons"]


def test_run_live_status_summarizes_blocking_risk_state(tmp_path, monkeypatch) -> None:
    from kairospy.infrastructure.configuration import PROJECT_STATE_DIR
    from kairospy.runtime import LiveRunDaemon
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_id = "venue-a-live"
    runtime_root = tmp_path / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    store = SQLiteRuntimeStore(RuntimePaths.under(runtime_root).runtime_database)
    at = datetime.now(timezone.utc)
    store.set_runtime_state(
        f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}",
        {
            "run_id": run_id,
            "phase": "running",
            "application_status": "running",
            "reason": "started",
            "services": [],
            "stop_requested": False,
        },
        at,
    )
    store.set_runtime_state("risk_runtime:last", {
        "run_id": run_id,
        "status": "blocking",
        "reasons": ("reconciliation_mismatch", "unknown_external_open_orders"),
        "unknown_external_open_order_count": 1,
    }, at)
    store.set_runtime_state(f"order_outbox_dispatcher:{run_id}", {
        "run_id": run_id,
        "phase": "running",
        "outbox_pending_count": 3,
        "outbox_dispatching_count": 1,
        "outbox_unknown_count": 0,
        "outbox_backlog_count": 4,
        "order_submit_latency_last_ms": 12.5,
        "order_submit_latency_max_ms": 20.0,
        "order_ack_latency_last_ms": 7.5,
        "order_ack_latency_max_ms": 10.0,
    }, at)
    store.set_runtime_state(f"fill_ingestion:{run_id}:last", {
        "run_id": run_id,
        "phase": "running",
        "fill_ingestion_latency_last_ms": 33.0,
        "fill_ingestion_latency_max_ms": 44.0,
    }, at)
    store.set_runtime_state(f"market_freshness:{run_id}:last", {
        "run_id": run_id,
        "phase": "running",
        "freshness_status": "healthy",
        "freshness_passed": True,
        "freshness_max_age_seconds": 60,
        "freshness_updated_age_seconds": 1.25,
        "market_event_age_seconds": 2.5,
        "channel_failure_count": 0,
    }, at)

    status = product_surface.run_live(SimpleNamespace(
        live_action="status",
        run_id=run_id,
        stale_after_seconds=5.0,
    ))

    assert status["status"] == "running"
    assert status["health"]["status"] == "blocking"
    assert status["health"]["healthy"] is False
    assert "risk_blocking" in status["health"]["reasons"]
    assert "unknown_external_open_orders" in status["health"]["reasons"]
    assert status["open_incident_count"] == 1
    assert status["incidents"][0]["incident_id"] == f"runtime-health:{run_id}"
    assert status["incidents"][0]["severity"] == "critical"
    metrics = product_surface.run_metrics(SimpleNamespace(run_id=run_id, stale_after_seconds=5.0))
    assert metrics["operation"] == "metrics"
    assert metrics["metrics"]["risk_blocked"] is True
    assert metrics["metrics"]["risk_reason_count"] == 2
    assert metrics["metrics"]["open_incident_count"] == 1
    assert metrics["metrics"]["outbox_backlog_count"] == 4
    assert metrics["metrics"]["order_submit_latency_last_ms"] == 12.5
    assert metrics["metrics"]["order_ack_latency_max_ms"] == 10.0
    assert metrics["metrics"]["fill_ingestion_latency_last_ms"] == 33.0
    assert metrics["metrics"]["market_freshness_status"] == "healthy"
    assert metrics["metrics"]["market_freshness_passed"] is True
    assert metrics["metrics"]["market_freshness_updated_age_seconds"] == 1.25
    assert metrics["metrics"]["market_event_age_seconds"] == 2.5
    metrics_artifact = tmp_path / "runtime" / "metrics.prom"
    exported_metrics = product_surface.run_metrics(SimpleNamespace(
        run_id=run_id,
        stale_after_seconds=5.0,
        output=metrics_artifact,
        prometheus=True,
    ))
    assert exported_metrics["metrics_format"] == "prometheus"
    assert exported_metrics["artifact"] == str(metrics_artifact)
    rendered_metrics = metrics_artifact.read_text(encoding="utf-8")
    assert 'kairospy_run_risk_blocked{run_id="venue-a-live"} 1' in rendered_metrics
    assert 'kairospy_run_open_incident_count{run_id="venue-a-live"} 1' in rendered_metrics
    assert 'kairospy_run_outbox_backlog_count{run_id="venue-a-live"} 4' in rendered_metrics
    assert 'kairospy_run_order_ack_latency_max_ms{run_id="venue-a-live"} 10.0' in rendered_metrics
    assert 'kairospy_run_fill_ingestion_latency_last_ms{run_id="venue-a-live"} 33.0' in rendered_metrics
    assert 'kairospy_run_market_freshness_passed{run_id="venue-a-live"} 1' in rendered_metrics
    assert 'kairospy_run_market_event_age_seconds{run_id="venue-a-live"} 2.5' in rendered_metrics
    assert 'kairospy_run_market_freshness_status{run_id="venue-a-live",status="healthy"} 1' in rendered_metrics
    assert 'kairospy_run_health_status{run_id="venue-a-live",status="blocking"} 1' in rendered_metrics
    assert exported_metrics["artifact_hash"]

    store.set_runtime_state("risk_runtime:last", {"run_id": run_id, "status": "ok", "reasons": ()}, at)
    recovered = product_surface.run_live(SimpleNamespace(
        live_action="status",
        run_id=run_id,
        stale_after_seconds=5.0,
    ))

    assert recovered["health"]["status"] == "ok"
    assert "incidents" not in recovered
    closed = store.runtime_incidents(run_id, status=None)
    assert len(closed) == 1
    assert closed[0].status == "closed"
    assert closed[0].close_reason == "runtime health recovered"


def test_run_incidents_and_close_incident_control_surface(tmp_path, monkeypatch) -> None:
    from kairospy.infrastructure.configuration import PROJECT_STATE_DIR
    from kairospy.runtime import LiveRunDaemon
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_id = "venue-a-live"
    runtime_root = tmp_path / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    store = SQLiteRuntimeStore(RuntimePaths.under(runtime_root).runtime_database)
    at = datetime.now(timezone.utc)
    store.set_runtime_state(
        f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}",
        {
            "run_id": run_id,
            "phase": "running",
            "application_status": "running",
            "reason": "started",
            "services": [],
            "stop_requested": False,
        },
        at,
    )
    store.set_runtime_state("risk_runtime:last", {
        "run_id": run_id,
        "status": "blocking",
        "reasons": ("unresolved_orders",),
    }, at)
    product_surface.run_status(SimpleNamespace(run_id=run_id, stale_after_seconds=5.0))

    incidents = product_surface.run_incidents(SimpleNamespace(run_id=run_id, status="open", limit=10))
    closed = product_surface.run_close_incident(SimpleNamespace(
        run_id=run_id,
        incident_id=f"runtime-health:{run_id}",
        actor="alice",
        reason="acknowledged and remediated",
    ))
    all_incidents = product_surface.run_incidents(SimpleNamespace(run_id=run_id, status="all", limit=10))

    assert incidents["operation"] == "incidents"
    assert incidents["incidents_count"] == 1
    assert incidents["incidents"][0]["status"] == "open"
    assert closed["operation"] == "close-incident"
    assert closed["incident"]["status"] == "closed"
    assert closed["incident"]["closed_by"] == "alice"
    assert all_incidents["incidents_count"] == 1
    assert all_incidents["incidents"][0]["status"] == "closed"


def test_run_export_writes_live_runtime_artifact_bundle(tmp_path, monkeypatch) -> None:
    from kairospy.infrastructure.configuration import PROJECT_STATE_DIR
    from kairospy.runtime import LiveRunDaemon
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    (tmp_path / "kairos.toml").write_text('[project]\nname = "live-run-contract"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_id = "venue-a-live"
    runtime_root = tmp_path / PROJECT_STATE_DIR / "runtime" / "live" / run_id
    store = SQLiteRuntimeStore(RuntimePaths.under(runtime_root).runtime_database)
    at = datetime.now(timezone.utc)
    store.set_runtime_state(
        f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}",
        {
            "run_id": run_id,
            "phase": "running",
            "application_status": "running",
            "reason": "started",
            "services": [],
            "stop_requested": False,
        },
        at,
    )
    store.set_runtime_state("risk_runtime:last", {"run_id": run_id, "status": "ok", "reasons": ()}, at)
    runtime_log = runtime_root / "runtime.jsonl"
    runtime_log.write_text(json.dumps({
        "schema_version": 1,
        "timestamp": at.isoformat(),
        "run_id": run_id,
        "level": "info",
        "event": "daemon_started",
        "payload": {},
        "record_hash": "0" * 64,
    }, sort_keys=True) + "\n", encoding="utf-8")
    export_dir = tmp_path / "runtime-export"

    export = product_surface.run_export(SimpleNamespace(
        run_id=run_id,
        output=export_dir,
        stale_after_seconds=5.0,
    ))

    manifest_path = Path(export["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    status = json.loads((export_dir / "status.json").read_text(encoding="utf-8"))
    metrics = json.loads((export_dir / "metrics.json").read_text(encoding="utf-8"))
    runtime_state = json.loads((export_dir / "runtime_state.json").read_text(encoding="utf-8"))

    assert export["operation"] == "export"
    assert export["status"] == "exported"
    assert manifest["run_id"] == run_id
    assert set(manifest["files"]) == {"status", "metrics", "commands", "incidents", "runtime_state", "runtime_log"}
    assert status["status"] == "running"
    assert metrics["run_status"] == "running"
    assert (export_dir / "runtime_log.jsonl").read_text(encoding="utf-8") == runtime_log.read_text(encoding="utf-8")
    assert f"{LiveRunDaemon.STATE_KEY_PREFIX}:{run_id}" in runtime_state["runtime_state"]
    assert manifest["artifact_hashes"]["status"] == export["artifact_hashes"]["status"]


def test_run_live_start_spawns_foreground_daemon_process_by_default(tmp_path, monkeypatch) -> None:
    from kairospy import Workspace, initialize_project
    from kairospy.runtime import LiveRunDaemon
    from kairospy.runtime.config import RuntimePaths
    from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
    from kairospy.surface import product as product_surface

    initialize_project(tmp_path, name="Live Spawn Contract")
    Workspace.open_or_create("alpha", start=tmp_path)
    (tmp_path / "strategy.py").write_text(
        "\n".join([
            "def workspace(context, params=None):",
            "    return context.project()",
            "",
            "class Strategy:",
            "    strategy_id = 'noop-live-spawn-test'",
            "",
            "    def on_start(self, context):",
            "        return ()",
            "",
            "    def on_market(self, context):",
            "        return ()",
            "",
            "    def on_fill(self, fill, context):",
            "        return ()",
            "",
            "    def on_end(self, context):",
            "        return ()",
            "",
            "def decide(context, params=None):",
            "    return Strategy()",
        ]) + "\n",
        encoding="utf-8",
    )
    run_config = tmp_path / "configs" / "runs" / "live.toml"
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text(
        "\n".join([
            "schema_version = 1",
            "",
            "[run]",
            'name = "live"',
            'mode = "live"',
            'workspace = "strategy:workspace"',
            'strategy = "strategy:decide"',
            "",
            "[bindings]",
            'account = "binance_live_spot"',
            'market = ["bars"]',
            'execution = "binance_live_spot"',
            "",
            "[live]",
            'provider = "binance"',
            'execution_driver = "binance-live"',
            'binding_id = "live-runtime-binding"',
            'recovery_binding_id = "live-recovery"',
            "",
            "[evidence]",
            'readiness = "governance:readiness/test-live.json"',
            'promotion = "governance:promotion/test-live.json"',
        ]) + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "kairos.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "live_trading_enabled = false",
            "live_trading_enabled = true",
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    class FakeProcess:
        pid = 4242

    with patch("kairospy.surface.product.subprocess.Popen", return_value=FakeProcess()) as popen:
        payload = product_surface.run_live(SimpleNamespace(
            live_action="start",
            run_id="venue-a-live",
            config=run_config,
            confirm_live=True,
            foreground=False,
            duration_seconds=None,
            poll_seconds=0.25,
            param=[],
        ))

    assert payload["status"] == "spawned"
    assert payload["pid"] == 4242
    assert payload["foreground"] is False
    assert "--foreground" in payload["command"]
    assert payload["command"].count("--foreground") == 1
    assert payload["runtime_database"].endswith("/.kairos/runtime/live/venue-a-live/runtime/runtime.sqlite3")
    assert payload["log_file"].endswith("/.kairos/runtime/live/venue-a-live/daemon.log")
    assert payload["structured_log_file"].endswith("/.kairos/runtime/live/venue-a-live/runtime.jsonl")
    assert popen.call_args.kwargs["start_new_session"] is True

    store = SQLiteRuntimeStore(RuntimePaths.under(tmp_path / ".kairos" / "runtime" / "live" / "venue-a-live").runtime_database)
    state = store.runtime_state(f"{LiveRunDaemon.STATE_KEY_PREFIX}:venue-a-live")
    assert isinstance(state, dict)
    assert state["phase"] == "starting"
    assert state["spawn"]["pid"] == 4242
    assert state["spawn"]["structured_log_file"] == payload["structured_log_file"]
    structured_records = [
        json.loads(line)
        for line in Path(payload["structured_log_file"]).read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [item["event"] for item in structured_records] == ["daemon_spawn_requested", "daemon_spawned"]
    assert all(item["run_id"] == "venue-a-live" for item in structured_records)

    status = product_surface.run_live(SimpleNamespace(
        live_action="status",
        run_id="venue-a-live",
        stale_after_seconds=5.0,
    ))
    assert status["status"] == "starting"
    assert status["phase"] == "starting"
    assert status["structured_log_file"] == payload["structured_log_file"]
    assert status["log_file"] == payload["log_file"]

    attach = product_surface.run_live(SimpleNamespace(
        live_action="attach",
        run_id="venue-a-live",
        stale_after_seconds=5.0,
    ))
    assert attach["live_action"] == "attach"
    assert attach["status"] == "starting"
    assert attach["log_file"] == payload["log_file"]

    unified_status = product_surface.run_status(SimpleNamespace(
        run_id="venue-a-live",
        stale_after_seconds=5.0,
    ))
    assert unified_status["operation"] == "status"
    assert unified_status["runtime_kind"] == "live"
    assert unified_status["status"] == "starting"

    stopped = product_surface.run_stop(SimpleNamespace(
        run_id="venue-a-live",
        actor="alice",
        reason="maintenance",
        timeout_seconds=2.5,
        force=True,
    ))
    assert stopped["operation"] == "stop"
    assert stopped["runtime_kind"] == "live"
    assert stopped["status"] == "stop_requested"
    assert stopped["operator_command"]["actor"] == "alice"
    assert stopped["operator_command"]["payload"]["timeout_seconds"] == 2.5
    assert stopped["operator_command"]["payload"]["force"] is True

    paused = product_surface.run_pause(SimpleNamespace(
        run_id="venue-a-live",
        actor="alice",
        reason="feed stale",
    ))
    assert paused["operation"] == "pause"
    assert paused["operator_command"]["command_type"] == "pause_new_orders"

    commands = product_surface.run_commands(SimpleNamespace(run_id="venue-a-live", limit=10))
    assert commands["operation"] == "commands"
    assert commands["commands_count"] >= 2
    assert commands["operator_commands"][-1]["command_type"] == "pause_new_orders"

    metrics = product_surface.run_metrics(SimpleNamespace(run_id="venue-a-live", stale_after_seconds=5.0))
    assert metrics["operation"] == "metrics"
    assert metrics["runtime_kind"] == "live"
    assert metrics["metrics"]["operator_command_count"] >= 2
    assert metrics["metrics"]["operator_command_backlog"] >= 2

    force_stop = product_surface.run_force_stop(SimpleNamespace(
        run_id="venue-a-live",
        actor="alice",
        reason="emergency",
        timeout_seconds=1.0,
    ))
    assert force_stop["operation"] == "force-stop"
    assert force_stop["force"] is True
    assert force_stop["operator_command"]["payload"]["force"] is True
