from __future__ import annotations

import stat
import textwrap
import time
from pathlib import Path

from kairospy.application.system import ComponentProcessApplication
from kairospy.application.workspace import WorkspaceApplication


def test_component_process_application_starts_bin_and_waits_for_health(tmp_path: Path) -> None:
    short_root = Path("/tmp/kairos-process-launch-test")
    if short_root.exists():
        import shutil
        shutil.rmtree(short_root)
    workspace = WorkspaceApplication().init(short_root / "w", workspace_id="launch-test")
    binary = short_root / "fake-execution"
    binary.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            import argparse, json, os, socket
            from pathlib import Path
            parser = argparse.ArgumentParser()
            parser.add_argument('--workspace', required=True)
            args = parser.parse_args()
            path = Path(args.workspace) / 'run' / 'execution' / 'execution.sock'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            server.listen(4)
            while True:
                client, _ = server.accept()
                request = client.recv(65536).decode()
                stopping = '/v1/stop' in request
                body = json.dumps({'status': 'stopping' if stopping else 'ready'}).encode()
                client.sendall(b'HTTP/1.1 202 Accepted\\r\\nContent-Length: ' + str(len(body)).encode() + b'\\r\\n\\r\\n' + body)
                client.close()
                if stopping:
                    break
            server.close()
            path.unlink(missing_ok=True)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    application = ComponentProcessApplication(workspace, binaries={"execution": str(binary)})
    control = application.ensure_running("execution")
    assert control.status()["status"] == "ready"
    assert application.stop("execution")["status"] == "stopping"
    time.sleep(0.05)
    import shutil
    shutil.rmtree(short_root, ignore_errors=True)


def test_component_status_does_not_start_a_missing_process(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="status")
    value = ComponentProcessApplication(workspace).status("market")
    assert value["status"] == "not_running"
    assert not workspace.paths.process_socket("market").exists()


def test_component_command_uses_instance_workspace_namespace(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="instance")
    instance = workspace.instance("backtest", "btc-sma", "run-001")
    command, _ = ComponentProcessApplication(workspace, binaries={"market": "market-bin"})._command(
        "market", account_id=None, instance_workspace=instance
    )

    assert command[-6:] == [
        "--launch-mode", "backtest", "--launch-id", "btc-sma", "--instance-id", "run-001"
    ]


def test_component_status_and_stop_use_instance_workspace(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="instance")
    instance = workspace.instance("paper", "btc-sma", "run-001")
    application = ComponentProcessApplication(workspace)

    status = application.status("execution", instance_workspace=instance)
    stopped = application.stop("execution", instance_workspace=instance)

    assert status["control_socket"] == str(instance.socket("execution"))
    assert stopped["control_socket"] == str(instance.socket("execution"))
