from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

from kairospy.system.apps.components.application import (
    ComponentProcessApplication,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.system.apps.workspace_services import WorkspaceServiceApplication


def test_component_process_application_starts_bin_and_waits_for_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ComponentProcessApplication, "_ensure_aeron_driver", lambda _self: None
    )
    short_root = Path("/tmp/kairos-process-launch-test")
    if short_root.exists():
        import shutil

        shutil.rmtree(short_root)
    workspace = WorkspaceApplication().init(
        short_root / "w", workspace_id="launch-test"
    )
    instance = workspace.instance("paper", "launch", "one")
    instance.prepare()
    binary = short_root / "fake-execution"
    binary.write_text(
        textwrap.dedent(
            f"""
            #!{sys.executable}
            import argparse, fcntl, json, os, socket, time
            from pathlib import Path
            parser = argparse.ArgumentParser()
            parser.add_argument('--workspace', required=True)
            parser.add_argument('--launch-mode', required=True)
            parser.add_argument('--launch-id', required=True)
            parser.add_argument('--instance-id', required=True)
            parser.add_argument('--aeron-dir', required=True)
            parser.add_argument('--aeron-channel', required=True)
            args = parser.parse_args()
            path = Path({str(instance.socket("execution"))!r})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            lock_path = Path({str(instance.lock("execution"))!r})
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock = lock_path.open('w')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            lock.write(str(os.getpid()))
            lock.flush()
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            server.listen(4)
            while True:
                client, _ = server.accept()
                request = client.recv(65536).decode()
                payload = json.loads(request.split('\\r\\n\\r\\n', 1)[1])
                stopping = payload.get('method') == 'system_stop'
                body = json.dumps({{
                    'jsonrpc': '2.0',
                    'id': payload.get('id'),
                    'result': {{'status': 'stopping' if stopping else 'ready'}},
                }}).encode()
                client.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: ' + str(len(body)).encode() + b'\\r\\n\\r\\n' + body)
                client.close()
                if stopping:
                    time.sleep(0.2)
                    break
            server.close()
            path.unlink(missing_ok=True)
            lock.close()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    application = ComponentProcessApplication(
        workspace, binaries={"execution": str(binary)}
    )
    control = application.ensure_running("execution", instance_workspace=instance)
    assert control.status()["status"] == "ready"
    assert (
        application.ensure_running("execution", instance_workspace=instance).status()[
            "status"
        ]
        == "ready"
    )
    started = time.monotonic()
    progress: list[str] = []
    control = application.restart(
        "execution", progress=progress.append, instance_workspace=instance
    )
    assert time.monotonic() - started >= 0.15
    assert control.status()["status"] == "ready"
    assert progress == [
        "Stopping execution...",
        "Waiting for execution to release its process lock (timeout: 15s)...",
        "execution stopped; starting replacement...",
        "execution restarted.",
    ]
    assert (
        application.stop("execution", instance_workspace=instance)["status"]
        == "stopping"
    )
    application._wait_stopped("execution", instance_workspace=instance)
    import shutil

    shutil.rmtree(short_root, ignore_errors=True)


def test_component_start_reports_early_exit_and_log_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ComponentProcessApplication, "_ensure_aeron_driver", lambda _self: None
    )
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="failed-start"
    )
    instance = workspace.instance("paper", "launch", "one")
    instance.prepare()
    binary = tmp_path / "fail-execution"
    binary.write_text(
        textwrap.dedent(
            f"""
            #!{sys.executable}
            import json
            print(json.dumps({{"fields": {{"error": "database migration failed"}}}}), flush=True)
            raise SystemExit(23)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    application = ComponentProcessApplication(
        workspace, binaries={"execution": str(binary)}, ready_timeout=10
    )
    started = time.monotonic()

    with pytest.raises(RuntimeError) as captured:
        application.ensure_running("execution", instance_workspace=instance)

    assert time.monotonic() - started < 5
    message = str(captured.value)
    assert "exited during startup with code 23" in message
    assert "database migration failed" in message
    assert "kairos launch artifacts launch --instance one" in message


def test_reference_startup_logs_support_redirected_text_output(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="redirected-reference-start"
    )
    application = ComponentProcessApplication(workspace, ready_timeout=1)
    log_path = workspace.paths.logs / "reference" / "process.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        '{"level":"ERROR","message":"unexpected argument --legacy"}\n',
        encoding="utf-8",
    )

    class ExitedProcess:
        @staticmethod
        def poll() -> int:
            return 2

    output = StringIO()
    control = application.client(
        "reference", workspace.paths.process_socket("reference"), timeout=0.1
    )
    with redirect_stdout(output), pytest.raises(RuntimeError) as captured:
        application._wait_ready(
            "reference",
            control,
            process=ExitedProcess(),
            log_path=log_path,
            initial_log_offset=0,
            stream_logs=True,
        )

    assert "unexpected argument --legacy" in output.getvalue()
    assert "exited during startup with code 2" in str(captured.value)
    assert "unexpected argument --legacy" in str(captured.value)


def test_component_start_rejects_responsive_process_without_route_declaration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ComponentProcessApplication, "_ensure_aeron_driver", lambda _self: None
    )
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="degraded-process"
    )
    socket = workspace.paths.process_socket("reference")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()

    class DegradedControl:
        def status(self) -> dict[str, str]:
            return {"status": "degraded"}

    control = DegradedControl()
    monkeypatch.setattr(
        ComponentProcessApplication,
        "client",
        lambda _self, _component, _socket, *, timeout: control,
    )

    with pytest.raises(RuntimeError, match="event-route declaration is missing"):
        ComponentProcessApplication(workspace).ensure_running("reference")


def test_component_restart_times_out_while_process_lock_is_held(
    tmp_path: Path,
) -> None:
    import subprocess

    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="stop-timeout"
    )
    lock_path = workspace.paths.process_lock("reference")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys,time; "
                "f=open(sys.argv[1], 'w'); "
                "fcntl.flock(f.fileno(), fcntl.LOCK_EX); "
                "f.write(str(os.getpid())); f.flush(); time.sleep(5)"
            ),
            str(lock_path),
        ]
    )
    try:
        for _ in range(50):
            if lock_path.exists() and lock_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.02)

        application = ComponentProcessApplication(workspace, stop_timeout=0.05)
        with pytest.raises(TimeoutError, match="did not stop within 0.05s"):
            application._wait_stopped("reference")
    finally:
        holder.terminate()
        holder.wait()


def test_component_status_does_not_start_a_missing_process(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="status"
    )
    value = ComponentProcessApplication(workspace).status("market")
    assert value["status"] == "not_running"
    assert value["control_reachable"] is False
    assert value["probe_error"] is None
    assert not workspace.paths.process_socket("market").exists()


def test_component_list_reports_workspace_components_without_starting_them(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="list")

    value = ComponentProcessApplication(workspace).list_status()

    assert set(value) == {"reference", "market"}
    assert all(item["status"] == "not_running" for item in value.values())
    assert not any(workspace.paths.process_socket(name).exists() for name in value)


def test_component_list_treats_a_stale_socket_as_not_running(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="stale"
    )
    socket = workspace.paths.process_socket("reference")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()

    value = ComponentProcessApplication(workspace).list_status()

    assert value["reference"]["status"] == "not_running"
    assert value["reference"]["control_reachable"] is False
    assert value["reference"]["control_socket_exists"] is True
    assert value["reference"]["probe_error"]


def test_component_list_includes_process_metadata_from_health_file(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="metadata"
    )
    health = workspace.paths.health_file("market")
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text('{"status":"ready","pid":999999}', encoding="utf-8")

    value = ComponentProcessApplication(workspace).list_status()["market"]

    assert value["pid"] == 999999
    assert value["pid_alive"] is False
    assert value["health_file"] == str(health)
    assert value["log_file"].endswith("logs/market/process.log")


def test_component_list_marks_dead_health_pid_as_stale(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="stale-pid"
    )
    health = workspace.paths.health_file("reference")
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text('{"status":"ready","pid":999999}', encoding="utf-8")

    value = ComponentProcessApplication(workspace).list_status()["reference"]

    assert value["status"] == "stale"
    assert value["pid_alive"] is False


def test_system_repair_removes_unlocked_stale_socket(tmp_path: Path) -> None:
    import shutil

    root = Path(f"/tmp/kairos-repair-{__import__('os').getpid()}")
    shutil.rmtree(root, ignore_errors=True)
    workspace = WorkspaceApplication().init(root, workspace_id="repair")
    socket = workspace.paths.process_socket("market")
    socket.parent.mkdir(parents=True, exist_ok=True)
    import socket as socket_module

    listener = socket_module.socket(socket_module.AF_UNIX)
    listener.bind(str(socket))
    listener.close()

    result = ComponentProcessApplication(workspace).repair()

    assert "market" in result["repaired"]
    assert not socket.exists()


def test_component_repair_only_removes_selected_stale_resources(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="component-repair"
    )
    sockets = {
        component: workspace.paths.process_socket(component)
        for component in ("market", "reference")
    }
    import socket as socket_module

    for socket in sockets.values():
        socket.parent.mkdir(parents=True, exist_ok=True)
        listener = socket_module.socket(socket_module.AF_UNIX)
        listener.bind(str(socket))
        listener.close()

    result = ComponentProcessApplication(workspace).repair_component("market")

    assert result == {"component": "market", "status": "repaired"}
    assert not sockets["market"].exists()
    assert sockets["reference"].exists()


def test_component_repair_removes_dead_health_metadata_without_socket(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="health-repair"
    )
    health = workspace.paths.health_file("market")
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text('{"pid": 999999, "status": "ready"}', encoding="utf-8")

    result = ComponentProcessApplication(workspace).repair_component("market")

    assert result == {"component": "market", "status": "repaired"}
    assert not health.exists()


def test_workspace_repair_start_is_idempotent_for_a_clean_stopped_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="clean-repair-start"
    )
    starts: list[str] = []

    def start(_self, component: str, **_options):
        starts.append(component)
        return {"component": component, "status": "ready"}

    monkeypatch.setattr(WorkspaceServiceApplication, "start_and_keep_running", start)

    result = WorkspaceServiceApplication(workspace).repair("reference", start=True)

    assert result == {"component": "reference", "status": "ready"}
    assert starts == ["reference"]


def test_system_repair_does_not_remove_lock_owned_socket(tmp_path: Path) -> None:
    import shutil

    root = Path(f"/tmp/kairos-repair-locked-{__import__('os').getpid()}")
    shutil.rmtree(root, ignore_errors=True)
    workspace = WorkspaceApplication().init(root, workspace_id="locked")
    socket_path = workspace.paths.process_socket("market")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    import socket as socket_module

    listener = socket_module.socket(socket_module.AF_UNIX)
    listener.bind(str(socket_path))
    lock_path = workspace.paths.process_lock("market")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    import subprocess

    holder = subprocess.Popen(
        [
            __import__("sys").executable,
            "-c",
            "import fcntl,os,sys,time; f=open(sys.argv[1], 'a+'); fcntl.flock(f.fileno(), fcntl.LOCK_EX); f.write(str(os.getpid())); f.flush(); time.sleep(5)",
            str(lock_path),
        ]
    )
    try:
        import time

        for _ in range(50):
            if lock_path.exists() and lock_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.02)
        result = ComponentProcessApplication(workspace).repair()
        assert "market" not in result["repaired"]
        assert socket_path.exists()
    finally:
        holder.terminate()
        holder.wait()
        listener.close()
        socket_path.unlink(missing_ok=True)


def test_runtime_supervisor_does_not_manage_instance_components(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="supervisor"
    )
    socket = workspace.paths.process_socket("execution")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()

    result = SystemRuntimeSupervisor(
        ComponentProcessApplication(workspace),
        desired={"execution": {}},
    ).reconcile_once()

    assert "execution" not in result
    assert socket.exists()


def test_runtime_supervisor_rejects_instance_component_registration(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="supervisor-boundary"
    )
    supervisor = SystemRuntimeSupervisor(ComponentProcessApplication(workspace))

    import pytest

    with pytest.raises(ValueError, match="launch-owned"):
        supervisor.register("execution")


def test_runtime_supervisor_persists_and_removes_desired_components(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="desired"
    )
    supervisor = SystemRuntimeSupervisor(ComponentProcessApplication(workspace))

    supervisor.register("market", {"market_runtime_profile": "primary-live"})
    assert "market" in supervisor.desired_path.read_text(encoding="utf-8")
    supervisor.unregister("market")
    assert supervisor.desired_path.read_text(encoding="utf-8") == "{}"


def test_component_command_uses_instance_workspace_namespace(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="instance"
    )
    instance = workspace.instance("backtest", "btc-sma", "run-001")
    command, environment = ComponentProcessApplication(
        workspace, binaries={"market": "market-bin"}
    )._command("market", account_id=None, instance_workspace=instance)

    assert command[-6:] == [
        "--launch-mode",
        "backtest",
        "--launch-id",
        "btc-sma",
        "--instance-id",
        "run-001",
    ]
    assert environment["AERON_DIR"] == str(workspace.paths.aeron_dir())


def test_market_command_passes_only_runtime_profile_selection(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="credential"
    )
    command, _ = ComponentProcessApplication(
        workspace, binaries={"market": "market-bin"}
    )._command(
        "market",
        account_id=None,
        market_runtime_profile="primary-live",
    )

    assert command[-2:] == ["--runtime-profile", "primary-live"]
    assert "--provider" not in command
    assert "--credential-id" not in command
    assert "--api-key" not in command
    assert "--secret" not in command


def test_execution_command_uses_instance_normalized_route_configuration(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="execution-routes"
    )
    command, _ = ComponentProcessApplication(
        workspace, binaries={"execution": "execution-bin"}
    )._command("execution", account_id=None)

    assert "--routes-json" not in command
    assert "--participant-id" not in command
    assert "--api-key" not in command
    assert "--secret" not in command


def test_risk_command_uses_instance_workspace_namespace(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="instance"
    )
    instance = workspace.instance("paper", "btc-sma", "run-001")
    command, _ = ComponentProcessApplication(
        workspace, binaries={"risk": "risk-bin"}
    )._command("risk", account_id=None, instance_workspace=instance)
    assert command[-6:] == [
        "--launch-mode",
        "paper",
        "--launch-id",
        "btc-sma",
        "--instance-id",
        "run-001",
    ]


def test_component_status_and_stop_use_instance_workspace(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="instance"
    )
    instance = workspace.instance("paper", "btc-sma", "run-001")
    application = ComponentProcessApplication(workspace)

    status = application.status("execution", instance_workspace=instance)
    stopped = application.stop("execution", instance_workspace=instance)

    assert status["control_socket"] == str(instance.socket("execution"))
    assert stopped["control_socket"] == str(instance.socket("execution"))


def test_workspace_service_start_registers_desired_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="service-start"
    )
    monkeypatch.setattr(
        ComponentProcessApplication,
        "ensure_running",
        lambda *_args, **_kwargs: type(
            "Control", (), {"status": lambda _self: {"status": "running"}}
        )(),
    )
    monkeypatch.setattr(SystemRuntimeSupervisor, "start_background", lambda _self: None)

    result = WorkspaceServiceApplication(workspace).start_and_keep_running("reference")

    desired = json.loads(
        (workspace.paths.run / "supervisor" / "desired.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "running"
    assert desired == {"reference": {}}


def test_workspace_service_restart_preserves_on_demand_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="service-restart"
    )
    monkeypatch.setattr(
        ComponentProcessApplication,
        "restart",
        lambda *_args, **_kwargs: type(
            "Control", (), {"status": lambda _self: {"status": "running"}}
        )(),
    )

    result = WorkspaceServiceApplication(workspace).restart("market")

    desired_path = workspace.paths.run / "supervisor" / "desired.json"
    assert result["status"] == "running"
    assert (
        not desired_path.exists()
        or json.loads(desired_path.read_text(encoding="utf-8")) == {}
    )


def test_workspace_service_stop_removes_desired_state_before_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="service-stop"
    )
    supervisor = SystemRuntimeSupervisor(ComponentProcessApplication(workspace))
    supervisor.register("market")

    def stop(_self, component: str) -> dict[str, str]:
        desired = json.loads(supervisor.desired_path.read_text(encoding="utf-8"))
        assert component not in desired
        return {"status": "not_running"}

    monkeypatch.setattr(ComponentProcessApplication, "stop", stop)

    result = WorkspaceServiceApplication(workspace).stop("market")

    assert result["status"] == "not_running"
    assert json.loads(supervisor.desired_path.read_text(encoding="utf-8")) == {}
