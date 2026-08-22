from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from kairospy.application.launch.application import LaunchRegistryApplication
from kairospy.application.system import NativeCliApplication
from kairospy.application.workspace import WorkspaceApplication
from kairospy.surface.cli import execute_argv


def _running_system_market(workspace) -> Path:
    socket = workspace.paths.process_socket("market")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()
    return socket


def _launch_market(workspace, *, view_root: bool = True):
    LaunchRegistryApplication(workspace).add("btc", mode="paper", instance_id="run-1")
    instance = workspace.instance("paper", "btc", "run-1")
    instance.prepare()
    socket = instance.socket("market")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.touch()
    view = instance.snapshot() if view_root else None
    fields = f'"socket":"{socket}"'
    if view is not None:
        fields += f',"view_root":"{view}"'
    instance.component_manifest().write_text(
        f'{{"components":{{"market":{{{fields}}}}},"accounts":{{}}}}',
        encoding="utf-8",
    )
    return instance, socket, view


def test_system_market_sources_uses_owner_connected_cli_with_typed_filters(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace", workspace_id="system-market-sources"
    )
    socket = _running_system_market(workspace)
    seen: list[tuple[str, list[str]]] = []

    def run(_self, component: str, arguments: list[str]):
        seen.append((component, arguments))
        return {"sources": []}

    monkeypatch.setattr(NativeCliApplication, "run", run)
    output = StringIO()

    assert (
        execute_argv(
            [
                "system",
                "component",
                "market",
                "sources",
                "--workspace",
                str(workspace.paths.root),
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--observation-kind",
                "quote",
                "--configured-only",
                "--format",
                "json",
            ],
            output,
        )
        == 0
    )

    value = json.loads(output.getvalue())
    assert value["scope"] == "system"
    assert seen == [
        (
            "market",
            [
                "connected",
                "sources",
                "--socket",
                str(socket),
                "--view-root",
                str(workspace.paths.child("snapshots", "market", "market-shared")),
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--observation-kind",
                "quote",
                "--configured-only",
            ],
        )
    ]


@pytest.mark.parametrize(
    ("command", "tail", "requires_view"),
    [
        (
            "sources",
            [
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--observation-kind",
                "quote",
                "--configured-only",
            ],
            False,
        ),
        (
            "snapshot",
            [
                "quote",
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--source-id",
                "binance-spot",
            ],
            True,
        ),
        (
            "freshness",
            [
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--source-id",
                "binance-spot",
                "--qualifier",
                "quote",
            ],
            True,
        ),
    ],
)
def test_launch_market_connected_commands_use_manifest_target(
    tmp_path: Path,
    monkeypatch,
    command: str,
    tail: list[str],
    requires_view: bool,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / command, workspace_id=f"launch-market-{command}"
    )
    _instance, socket, view_root = _launch_market(workspace)
    seen: list[tuple[str, list[str]]] = []

    def run(_self, component: str, arguments: list[str]):
        seen.append((component, arguments))
        return {"status": "ready"}

    monkeypatch.setattr(NativeCliApplication, "run", run)
    argv = ["launch", "instance", "component", "market", command, "btc"]
    if command == "snapshot":
        argv.append("quote")
    argv.extend(tail[1:] if command == "snapshot" else tail)
    argv.extend(("--workspace", str(workspace.paths.root), "--format", "json"))
    output = StringIO()

    assert execute_argv(argv, output) == 0
    value = json.loads(output.getvalue())
    assert value["scope"] == "launch-instance"
    assert value["launch_id"] == "btc"
    assert value["instance_id"] == "run-1"
    expected_target = ["--socket", str(socket), "--view-root", str(view_root)]
    assert seen == [("market", ["connected", command, *expected_target, *tail])]
    assert requires_view is (command != "sources")


def test_launch_market_view_command_rejects_manifest_without_view_root(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="launch-market-no-view"
    )
    _launch_market(workspace, view_root=False)
    output = StringIO()

    assert (
        execute_argv(
            [
                "launch",
                "instance",
                "component",
                "market",
                "snapshot",
                "btc",
                "quote",
                "--market-id",
                "market:binance:spot:BTCUSDT",
                "--source-id",
                "binance-spot",
                "--workspace",
                str(workspace.paths.root),
            ],
            output,
        )
        != 0
    )
    assert "has no view root" in output.getvalue()
