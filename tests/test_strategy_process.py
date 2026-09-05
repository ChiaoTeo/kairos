from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
import pytest

from kairospy.system.apps.launch import StrategyProcessController
from kairospy.system.apps.launch.application import strategy_process as process_module
from kairospy.system.apps.launch.composition import compose_strategy_process
from kairospy.system.apps.launch import LaunchControlApplication
from kairospy.system.apps.launch.composition import release_strategy_market_owner
from kairospy.system.apps.components.application import UnixRestClient
from kairospy.system.apps.components.application.event_routes import (
    ensure_instance_event_route,
    ensure_workspace_event_route,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication
from kairospy.strategy import StrategyCommand
from kairospy.strategy import CommandResult


def _write_component_manifest(
    instance,
    *,
    components: dict[str, dict[str, str]],
    accounts: dict[str, dict[str, str]] | None = None,
) -> None:
    workspace_route = ensure_workspace_event_route(instance.workspace)
    instance_route = ensure_instance_event_route(instance)
    routed_components: dict[str, dict[str, str]] = {}
    for name, value in components.items():
        route = (
            workspace_route
            if name == "reference"
            or (
                name == "market"
                and value.get("socket")
                == str(instance.workspace.paths.process_socket("market"))
            )
            else instance_route
        )
        routed_components[name] = {**value, "event_route": route.route_id}
    routed_accounts = {
        account_id: {**value, "event_route": instance_route.route_id}
        for account_id, value in (accounts or {}).items()
    }
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workspace_id": instance.workspace.workspace_id,
                "launch_id": instance.launch_id,
                "instance_id": instance.instance_id,
                "mode": instance.mode,
                "event_routes": {
                    workspace_route.route_id: workspace_route.as_manifest(),
                    instance_route.route_id: instance_route.as_manifest(),
                },
                "components": routed_components,
                "accounts": routed_accounts,
            }
        ),
        encoding="utf-8",
    )


def test_strategy_process_starts_without_snapshot_event_join(
    tmp_path: Path,
) -> None:
    root = tmp_path / "strategy-process"
    workspace = WorkspaceApplication().init(root / "w", workspace_id="sp")
    (workspace.paths.root / "user_strategy.py").write_text(
        "from kairospy.strategy import Strategy\n"
        "class UserStrategy(Strategy):\n"
        "    strategy_id = 'process-strategy'\n",
        encoding="utf-8",
    )
    instance = workspace.instance("paper", "l", "i")
    instance.prepare()
    instance.normalized_config().parent.mkdir(parents=True, exist_ok=True)
    instance.normalized_config().write_text(
        json.dumps({"market_scope": "shared", "execution": {"enabled": False}}),
        encoding="utf-8",
    )
    _write_component_manifest(
        instance,
        components={
            "market": {"socket": str(workspace.paths.process_socket("market"))}
        },
    )
    process = StrategyProcessController(workspace, ready_timeout=5)
    socket = process.ensure_running(
        "user_strategy:UserStrategy",
        launch_id="l",
        instance_id="i",
    )
    metadata_path = instance.paths.process_dir("strategy") / "process.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    pid = int(metadata["pid"])
    assert (
        workspace.instance("paper", "l", "i").log("strategy", "process.log").is_file()
    )
    assert not (
        workspace.paths.logs / "launches" / "paper" / "l" / "i" / "strategy.log"
    ).exists()
    try:
        started = asyncio.run(UnixRestClient(socket).request("POST", "/v1/start"))
        assert started["status"] == "ready"
        assert started["reason"] is None
    finally:
        assert process.stop("l", "i")["status"] == "stopped"
    assert not metadata_path.exists()
    observed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not observed or observed.startswith("Z")


def test_strategy_start_timeout_terminates_the_spawned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-timeout"
    )
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()

    class FakeProcess:
        pid = 424_242

        @staticmethod
        def poll() -> None:
            return None

    async def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("control socket unavailable")

    terminated: list[tuple[int, float]] = []
    monkeypatch.setattr(
        process_module.subprocess, "Popen", lambda *_a, **_k: FakeProcess()
    )
    monkeypatch.setattr(process_module.UnixRestClient, "request", unavailable)
    monkeypatch.setattr(
        process_module,
        "_terminate_process",
        lambda pid, timeout: terminated.append((pid, timeout)),
    )
    controller = StrategyProcessController(workspace, ready_timeout=0.01)

    with pytest.raises(TimeoutError, match="did not become ready"):
        controller.ensure_running(
            "user_strategy:UserStrategy",
            launch_id="launch",
            instance_id="instance",
        )

    assert terminated == [(424_242, 0.01)]
    assert not (instance.paths.process_dir("strategy") / "process.json").exists()


def test_strategy_stop_uses_owned_pid_when_control_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-stop-fallback"
    )
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()
    metadata_path = instance.paths.process_dir("strategy") / "process.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text("{}", encoding="utf-8")
    terminated = False

    async def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("control socket unavailable")

    def terminate(_pid: int, _timeout: float) -> None:
        nonlocal terminated
        terminated = True

    monkeypatch.setattr(process_module.UnixRestClient, "request", unavailable)
    monkeypatch.setattr(process_module, "_owned_process_pid", lambda *_a, **_k: 424_242)
    monkeypatch.setattr(process_module, "_terminate_process", terminate)
    monkeypatch.setattr(
        process_module,
        "_wait_process_exit",
        lambda *_a, **_k: terminated,
    )

    result = StrategyProcessController(workspace, ready_timeout=0.01).stop(
        "launch", "instance"
    )

    assert result["status"] == "stopped"
    assert result["control_error"] == "control socket unavailable"
    assert terminated
    assert not metadata_path.exists()


def test_launch_status_and_stop_are_safe_when_instance_is_not_running(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-status"
    )
    application = LaunchControlApplication(workspace)
    target = application.target("launch", "instance")
    assert application.status(target)["status"] == "not_running"
    assert application.stop(target)["status"] == "not_running"


def test_launch_cleanup_releases_subscriptions_for_dead_strategy_process(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="dead-strategy"
    )
    instance = workspace.instance("paper", "launch", "instance")
    instance.prepare()
    market_socket = workspace.paths.process_socket("market")
    instance.component_manifest().write_text(
        json.dumps({"components": {"market": {"socket": str(market_socket)}}}),
        encoding="utf-8",
    )
    instance.lifecycle_journal().parent.mkdir(parents=True, exist_ok=True)
    instance.lifecycle_journal().write_text(
        json.dumps(
            {
                "launch_id": "launch",
                "instance_id": "instance",
                "strategy_id": "orphaned-strategy",
                "state": "running",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[dict[str, str]] = []

    class Port:
        def release_owner(self, **kwargs):
            kwargs.pop("launch_id", None)
            calls.append(kwargs)
            return type(
                "ReleaseResponse",
                (),
                {"released_subscription_ids": ["subscription-1"]},
            )()

    class Client:
        def __init__(self, _socket):
            self.control = Port()

    monkeypatch.setattr(
        "kairospy.system.apps.launch.composition.MarketSystemClient",
        Client,
    )
    result = release_strategy_market_owner(workspace, instance)

    assert result is not None and result["status"] == "applied"
    assert calls == [
        {
            "strategy_id": "orphaned-strategy",
            "instance_id": "instance",
            "request_id": "orphaned-strategy:instance:market.release_owner:external-stop",
        }
    ]


def test_launch_status_includes_registered_state_when_instance_is_not_running(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-registry"
    )
    application = LaunchControlApplication(workspace)
    from kairospy.system.apps.launch import LaunchRegistryApplication

    LaunchRegistryApplication(workspace).add(
        "launch", instance_id="instance", strategy_ref="user:Strategy"
    )
    value = application.status(application.target("launch", "instance"))

    assert value["status"] == "not_running"
    assert value["registry_state"] == "created"
    assert value["registry_consistent"] is True


def test_strategy_composition_uses_instance_market_and_account_resources(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-resources"
    )
    (workspace.paths.root / "user_strategy.py").write_text(
        "from kairospy.strategy import Strategy\n"
        "class UserStrategy(Strategy):\n"
        "    strategy_id = 'resource-strategy'\n",
        encoding="utf-8",
    )
    instance = workspace.instance("backtest", "launch", "run-1")
    instance.prepare()
    _write_component_manifest(
        instance,
        components={
            "market": {"socket": str(instance.socket("market"))},
            "execution": {"socket": str(instance.socket("execution"))},
        },
    )
    composition = compose_strategy_process(
        workspace,
        strategy_ref="user_strategy:UserStrategy",
        launch_id="launch",
        instance_id="run-1",
        mode="backtest",
    )
    assert type(composition.application.context.market._snapshots).__module__ == (
        "kairospy._native_market_contract"
    )
    assert composition.application.context.market._snapshots.path.is_relative_to(
        instance.snapshot()
    )
    assert (
        composition.application.context.execution._commands.client.socket_path
        == instance.paths.process_socket("execution")
    )


def test_interactive_strategy_composes_without_execution_or_accounts(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-interactive"
    )
    instance = workspace.instance("paper", "manual", "run-1")
    instance.prepare()
    instance.normalized_config().parent.mkdir(parents=True, exist_ok=True)
    instance.normalized_config().write_text(
        json.dumps({"market_scope": "shared", "execution": {"enabled": False}}),
        encoding="utf-8",
    )
    _write_component_manifest(
        instance,
        components={
            "market": {"socket": str(workspace.paths.process_socket("market"))}
        },
    )

    composition = compose_strategy_process(
        workspace,
        strategy_ref="builtin:interactive",
        launch_id="manual",
        instance_id="run-1",
    )

    assert composition.entrypoint.strategy.strategy_id == "builtin-interactive"
    assert composition.application.context.account is not None
    assert (
        type(composition.application.context.market._live_source).__name__
        == "MarketLiveSubscription"
    )
    composition.application.start()
    result = asyncio.run(
        composition.application.command(
            StrategyCommand("command-1", "interactive.python", "1 + 1")
        )
    )
    assert result.status == "completed"
    assert result.result["value"] == 2
    from decimal import Decimal
    from kairospy.strategy import ImmediateAlgorithm, InstrumentId

    disabled = composition.application.context.execution.target_position(
        InstrumentId("instrument:test:BTCUSDT"),
        Decimal("1"),
        account="main",
        algorithm=ImmediateAlgorithm(),
    )
    assert disabled.status == "rejected"
    assert disabled.error == "execution is disabled for this launch"


def test_authoritative_config_requires_enabled_execution_connection(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-required-execution"
    )
    instance = workspace.instance("paper", "launch", "run-1")
    instance.prepare()
    instance.normalized_config().parent.mkdir(parents=True, exist_ok=True)
    instance.normalized_config().write_text(
        json.dumps(
            {
                "launch": {"id": "launch", "mode": "paper"},
                "execution": {"enabled": True},
                "market_scope": "instance",
            }
        ),
        encoding="utf-8",
    )
    _write_component_manifest(
        instance,
        components={"market": {"socket": str(instance.socket("market"))}},
    )

    with pytest.raises(RuntimeError, match="Execution is enabled"):
        compose_strategy_process(
            workspace,
            strategy_ref="builtin:interactive",
            launch_id="launch",
            instance_id="run-1",
        )


def test_disabled_execution_ignores_a_residual_manifest_connection(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init(
        tmp_path / "workspace", workspace_id="sp-disabled-execution"
    )
    instance = workspace.instance("paper", "launch", "run-1")
    instance.prepare()
    instance.normalized_config().parent.mkdir(parents=True, exist_ok=True)
    instance.normalized_config().write_text(
        json.dumps(
            {
                "launch": {"id": "launch", "mode": "paper"},
                "execution": {"enabled": False},
                "market_scope": "instance",
            }
        ),
        encoding="utf-8",
    )
    _write_component_manifest(
        instance,
        components={
            "market": {"socket": str(instance.socket("market"))},
            "execution": {"socket": str(instance.socket("execution"))},
        },
    )

    composition = compose_strategy_process(
        workspace,
        strategy_ref="builtin:interactive",
        launch_id="launch",
        instance_id="run-1",
    )

    assert composition.application.context.execution._commands is None
