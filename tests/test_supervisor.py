import asyncio
import os
import sys
from pathlib import Path

from kairospy.application.system import (
    ProcessSpec,
    ProcessState,
    ProcessSupervisor,
    ReferenceProcessConfig,
    RiskProcessConfig,
    UnixRestClient,
)
from kairospy.application.workspace import WorkspaceApplication


def test_reference_process_config_builds_business_process_spec(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    spec = ReferenceProcessConfig(
        workspace=workspace,
        provider="default",
        once=True,
    ).process_spec()
    assert spec.name == "reference"
    assert spec.command[-1] == "--once"
    assert "--provider" in spec.command
    assert spec.command[spec.command.index("--provider") + 1] == "default"
    assert "--database" not in spec.command
    assert "--health-file" in spec.command
    assert "--socket" in spec.command
    assert spec.control_socket == workspace.paths.reference_socket()


def test_process_supervisor_starts_and_stops_child_process() -> None:
    async def scenario() -> None:
        supervisor = ProcessSupervisor()
        spec = ProcessSpec("worker", (sys.executable, "-c", "import time; time.sleep(30)"))
        await supervisor.start(spec)
        assert supervisor.statuses()["worker"] is ProcessState.RUNNING
        await supervisor.stop("worker")
        assert supervisor.statuses()["worker"] is ProcessState.EXITED
        await supervisor.shutdown()

    asyncio.run(scenario())


def test_supervisor_can_wait_for_process_owned_health(tmp_path: Path) -> None:
    async def scenario() -> None:
        health = tmp_path / "health.json"
        code = "import json,sys,time; f=open(sys.argv[1], 'w'); json.dump({'status':'ready','actor_id':'test'}, f); f.close(); time.sleep(30)"
        supervisor = ProcessSupervisor()
        spec = ProcessSpec("healthy", (sys.executable, "-c", code, str(health)), health_file=health)
        result = await supervisor.start_ready(spec, timeout=2)
        assert result["actor_id"] == "test"
        await supervisor.stop("healthy")

    asyncio.run(scenario())


def test_supervisor_stops_reference_through_control_socket_before_signal() -> None:
    async def scenario() -> None:
        socket = Path(f"/tmp/kairos-supervisor-stop-{os.getpid()}.sock")
        socket.unlink(missing_ok=True)
        child = (
            "import os,socket,sys; "
            "p=sys.argv[1]; "
            "s=socket.socket(socket.AF_UNIX); s.bind(p); s.listen(1); "
            "c,_=s.accept(); c.recv(65536); "
            "b=b'{\"status\":\"stopping\"}'; "
            "c.sendall(b'HTTP/1.1 202 Accepted\\r\\nContent-Length: '+str(len(b)).encode()+b'\\r\\n\\r\\n'+b); "
            "c.close(); s.close(); os.unlink(p)"
        )
        supervisor = ProcessSupervisor()
        spec = ProcessSpec(
            "reference",
            (sys.executable, "-c", child, str(socket)),
            control_socket=socket,
            stop_timeout=5,
        )
        await supervisor.start(spec)
        for _ in range(100):
            if socket.exists():
                break
            await asyncio.sleep(0.01)
        assert socket.exists()
        await supervisor.stop("reference")
        assert supervisor.statuses()["reference"] is ProcessState.EXITED
        await supervisor.shutdown()

    asyncio.run(scenario())


def test_reference_process_leaves_provider_endpoint_defaults_to_rust(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    spec = ReferenceProcessConfig(
        workspace=workspace,
        provider="binance-options",
    ).process_spec()
    assert "--endpoint" not in spec.command


def test_reference_process_forwards_explicit_endpoint(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    spec = ReferenceProcessConfig(
        workspace=workspace,
        provider="binance-options",
        endpoint="https://reference.example.test",
    ).process_spec()
    endpoint_index = spec.command.index("--endpoint") + 1
    assert spec.command[endpoint_index] == "https://reference.example.test"


def test_massive_reference_spec_uses_full_universe_without_underlying_filter(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    spec = ReferenceProcessConfig(
        workspace=workspace,
        provider="massive-options",
        api_key="massive-secret",
    ).process_spec()
    assert "massive-secret" not in spec.command
    assert spec.environment["MASSIVE_API_KEY"] == "massive-secret"
    assert "MASSIVE_OPTION_UNDERLYING" not in spec.environment
    assert spec.health_file == workspace.paths.reference_health()
    assert "--endpoint" not in spec.command


def test_risk_process_spec_contains_only_process_controls(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace")
    instance = workspace.instance("paper", "demo", "run-001")
    spec = RiskProcessConfig(
        workspace=workspace,
        instance_workspace=instance,
    ).process_spec()
    assert spec.name == "risk"
    assert spec.command[:2] == ("kairos-risk", "--workspace")
    assert spec.command[spec.command.index("--workspace") + 1] == str(workspace.paths.root)
    assert "--interval-ms" in spec.command
    assert "--socket" not in spec.command
    assert "--health" not in spec.command
    assert spec.control_socket == instance.socket("risk")


def test_unix_rest_client_round_trips_http_over_socket(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket = Path(f"/tmp/kairos-supervisor-test-{os.getpid()}.sock")

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            body = b'{"status":"ok","generation":3}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=str(socket))
        try:
            response = await UnixRestClient(socket).health()
            assert response == {"status": "ok", "generation": 3}
        finally:
            server.close()
            await server.wait_closed()
            socket.unlink(missing_ok=True)

    asyncio.run(scenario())
