from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

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

    assert parsed.group == "run"
    assert parsed.action == "live"
    assert parsed.live_action == "status"
    assert parsed.run_id == "venue-a-live"
    assert configured.config == Path("configs/runs/live.toml")
    assert configured.param == ["risk=low"]
    assert configured.confirm_live is True
    assert kill.live_action == "kill-switch"
    assert kill.actor == "alice"
    assert kill.reason == "risk breach"
    assert reload_risk.live_action == "reload-risk-limits"
    assert reload_risk.risk_limits_hash == "limits-hash"
    assert "live_" + "run_id" not in vars(parsed)


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
