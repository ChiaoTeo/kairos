from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import kairospy.surface.cli.commands.launch as launch_commands
import kairospy.surface.cli.commands.system as system_commands
from kairospy.application.support.launch.control.attach import AttachTarget, LaunchAttachSession, read_file_chunk, resolve_attach_target
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.surface.interactive.attach import parse_attach_shell_command
from kairospy.surface.tui.attach import AttachViewModel, RuntimeAttachApp, summary_text


def test_resolve_system_attach_target_uses_current_instance(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    directory = root / "system" / "kairos-system" / "instances" / "one"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps({"mode": "system", "launch_id": "kairos-system", "phase": "running"}), encoding="utf-8")
    (directory / "summary.json").write_text(json.dumps({"strategy_id": "system"}), encoding="utf-8")
    (directory / "launch.log").write_text("started\n", encoding="utf-8")
    (root / "system" / "kairos-system" / "current.json").write_text(
        json.dumps({"directory": str(directory)}),
        encoding="utf-8",
    )

    target = resolve_attach_target(target=None, mode=RuntimeMode.SYSTEM, launch_id="kairos-system", root=root)

    assert target.mode is RuntimeMode.SYSTEM
    assert target.launch_id == "kairos-system"
    assert target.directory == directory
    assert target.log_file == directory / "launch.log"


def test_resolve_attach_target_infers_non_system_mode_from_launch_id(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    directory = root / "paper" / "binance-equity-aapl-paper" / "instances" / "one"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps({"mode": "paper", "launch_id": "binance-equity-aapl-paper", "phase": "running"}),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(json.dumps({"strategy_id": "paper"}), encoding="utf-8")
    (directory / "launch.log").write_text("started\n", encoding="utf-8")
    (root / "paper" / "binance-equity-aapl-paper" / "current.json").write_text(
        json.dumps({"directory": str(directory)}),
        encoding="utf-8",
    )

    target = resolve_attach_target(target=None, mode=None, launch_id="binance-equity-aapl-paper", root=root)

    assert target.mode is RuntimeMode.PAPER
    assert target.launch_id == "binance-equity-aapl-paper"
    assert target.directory == directory


def test_read_file_chunk_tracks_position(tmp_path: Path) -> None:
    log = tmp_path / "launch.log"
    log.write_text("one\n", encoding="utf-8")

    chunk, position = read_file_chunk(log, 0)
    assert chunk == "one\n"

    log.write_text("one\ntwo\n", encoding="utf-8")
    chunk, position = read_file_chunk(log, position)
    assert chunk == "two\n"


def test_attach_session_status_reads_state_and_records(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    directory = root / "system" / "kairos-system" / "instances" / "one"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps({"mode": "system", "launch_id": "kairos-system", "phase": "running", "heartbeat_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(json.dumps({"strategy_id": "system"}), encoding="utf-8")
    (root / "system" / "kairos-system" / "current.json").write_text(json.dumps({"directory": str(directory)}), encoding="utf-8")
    session = LaunchAttachSession.system(root=root)

    status = session.status()

    assert status["mode"] == "system"
    assert status["launch_id"] == "kairos-system"
    assert status["phase"] == "running"
    assert status["record_count"] == 1


def test_attach_session_refreshes_current_instance_and_log(tmp_path: Path) -> None:
    root = tmp_path / "launches"
    first = root / "system" / "kairos-system" / "instances" / "one"
    second = root / "system" / "kairos-system" / "instances" / "two"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "state.json").write_text(json.dumps({"phase": "running", "heartbeat_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    (first / "summary.json").write_text(json.dumps({"strategy_id": "system"}), encoding="utf-8")
    (first / "launch.log").write_text("old\n", encoding="utf-8")
    (second / "state.json").write_text(json.dumps({"phase": "running", "heartbeat_at": "2026-01-01T00:00:01+00:00"}), encoding="utf-8")
    (second / "summary.json").write_text(json.dumps({"strategy_id": "system"}), encoding="utf-8")
    (second / "launch.log").write_text("new\n", encoding="utf-8")
    current = root / "system" / "kairos-system" / "current.json"
    current.write_text(json.dumps({"directory": str(first)}), encoding="utf-8")
    session = LaunchAttachSession.system(root=root)
    old_chunk = session.read_log_since(0)

    current.write_text(json.dumps({"directory": str(second)}), encoding="utf-8")
    status = session.status()
    new_chunk = session.read_log_since(old_chunk.position)

    assert status["directory"] == str(second)
    assert new_chunk.text == "new\n"


def test_attach_view_model_formats_status_for_structured_widgets() -> None:
    view = AttachViewModel.from_status(
        {
            "mode": "system",
            "launch_id": "kairos-system",
            "phase": "running",
            "heartbeat_at": "2026-01-01T00:00:00+00:00",
            "heartbeat_age_seconds": 65,
            "directory": "/tmp/runtime",
            "state_file": "/tmp/runtime/state.json",
            "log_file": "/tmp/runtime/launch.log",
            "records": [{"phase": "running"}],
        }
    )

    assert view.target_label == "system:kairos-system"
    assert view.heartbeat_label == "2026-01-01T00:00:00+00:00 (1m 5s)"
    assert view.record_count == "1"


def test_textual_attach_app_maps_generic_command(tmp_path: Path) -> None:
    session = FakeAttachSession(RuntimeMode.SYSTEM, tmp_path)
    app = RuntimeAttachApp(session)  # type: ignore[arg-type]

    label, callback = app._operation_for_command('command account.current \'{"account":"main"}\'')

    assert label == "account.current"
    assert callback() == {"kind": "account.current", "payload": {"account": "main"}, "wait": True}


def test_textual_attach_app_maps_system_trade_shortcuts(tmp_path: Path) -> None:
    session = FakeAttachSession(RuntimeMode.SYSTEM, tmp_path)
    app = RuntimeAttachApp(session)  # type: ignore[arg-type]

    label, callback = app._operation_for_command("trade-acquire main")

    assert label == "trade-acquire"
    assert callback() == {"kind": "account.trade-acquire", "payload": {"account": "main"}, "wait": True}


def test_textual_attach_app_rejects_trade_shortcuts_for_strategy_launch(tmp_path: Path) -> None:
    session = FakeAttachSession(RuntimeMode.PAPER, tmp_path)
    app = RuntimeAttachApp(session)  # type: ignore[arg-type]

    try:
        app._operation_for_command("trade-status main")
    except ValueError as error:
        assert "only available for system attach" in str(error)
    else:
        raise AssertionError("expected trade-status to be rejected for non-system attach")


def test_textual_attach_summary_makes_heartbeat_visible() -> None:
    text = summary_text(
        {
            "mode": "system",
            "launch_id": "kairos-system",
            "phase": "running",
            "heartbeat_at": "2026-01-01T00:00:00+00:00",
            "heartbeat_age_seconds": 1.25,
            "directory": "/tmp/runtime",
            "log_file": "/tmp/runtime/launch.log",
        }
    )

    assert "heartbeat=2026-01-01T00:00:00+00:00" in text
    assert "age=1.2s" in text


def test_attach_shell_parser_preserves_existing_command_shape(tmp_path: Path) -> None:
    session = FakeAttachSession(RuntimeMode.SYSTEM, tmp_path)

    parsed = parse_attach_shell_command(
        session,  # type: ignore[arg-type]
        'command account.current --payload-json \'{"account":"main"}\' --timeout 2',
    )

    assert parsed == {
        "kind": "account.current",
        "payload": {"account": "main"},
        "wait": True,
        "timeout_seconds": 2.0,
    }
    assert parse_attach_shell_command(session, "trade-status main")["kind"] == "account.trade-status"  # type: ignore[arg-type]


def test_system_attach_opens_runtime_attach_app(monkeypatch, tmp_path: Path) -> None:
    root = _write_launch(root=tmp_path / "launches", mode=RuntimeMode.SYSTEM, launch_id="kairos-system")
    opened: list[LaunchAttachSession] = []

    class FakeApp:
        def __init__(self, session: LaunchAttachSession) -> None:
            opened.append(session)

        def run(self) -> None:
            return None

    monkeypatch.setattr(system_commands, "RuntimeAttachApp", FakeApp)

    system_commands.attach(None, root=root, launch_id="kairos-system", shell=False)

    assert opened[0].target.mode is RuntimeMode.SYSTEM
    assert opened[0].target.launch_id == "kairos-system"


def test_launch_attach_opens_runtime_attach_app(monkeypatch, tmp_path: Path) -> None:
    root = _write_launch(root=tmp_path / "launches", mode=RuntimeMode.PAPER, launch_id="paper-main")
    opened: list[LaunchAttachSession] = []

    class FakeApp:
        def __init__(self, session: LaunchAttachSession) -> None:
            opened.append(session)

        def run(self) -> None:
            return None

    monkeypatch.setattr(launch_commands, "RuntimeAttachApp", FakeApp)

    launch_commands.attach(None, mode=RuntimeMode.PAPER, launch_id="paper-main", root=root, shell=False)

    assert opened[0].target.mode is RuntimeMode.PAPER
    assert opened[0].target.launch_id == "paper-main"


class FakeAttachSession:
    def __init__(self, mode: RuntimeMode, root: Path) -> None:
        self.target = AttachTarget(
            mode=mode,
            launch_id="test-launch",
            root=root,
            directory=root,
            state_file=root / "state.json",
            log_file=root / "launch.log",
        )

    def submit_command(
        self,
        kind: str,
        payload: Mapping[str, object] | None = None,
        *,
        wait: bool = True,
        timeout_seconds: float = 5.0,
    ) -> Mapping[str, object]:
        _ = timeout_seconds
        return {"kind": kind, "payload": dict(payload or {}), "wait": wait}

    def trade_acquire(self, account: str) -> Mapping[str, object]:
        return self.submit_command("account.trade-acquire", {"account": account})

    def trade_status(self, account: str | None = None) -> Mapping[str, object]:
        return self.submit_command("account.trade-status", {"account": account} if account else {})

    def trade_release(self, account: str) -> Mapping[str, object]:
        return self.submit_command("account.trade-release", {"account": account})


def _write_launch(*, root: Path, mode: RuntimeMode, launch_id: str) -> Path:
    directory = root / mode.value / launch_id / "instances" / "one"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(
        json.dumps({"mode": mode.value, "launch_id": launch_id, "phase": "running"}),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(json.dumps({"strategy_id": "test"}), encoding="utf-8")
    (directory / "launch.log").write_text("started\n", encoding="utf-8")
    (root / mode.value / launch_id / "current.json").write_text(json.dumps({"directory": str(directory)}), encoding="utf-8")
    return root
