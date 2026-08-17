from __future__ import annotations

import asyncio
import os
import shutil
import json
from pathlib import Path
import pytest

from kairospy.application.launch import StrategyProcessController
from kairospy.application.strategy.composition import compose_strategy_process
from kairospy.application.launch import LaunchControlApplication
from kairospy.application.launch.composition import release_strategy_market_owner
from kairospy.application.system import UnixRestClient
from kairospy.application.workspace import WorkspaceApplication
from kairospy.strategy import StrategyCommand
from kairospy.strategy import CommandResult


def test_strategy_process_starts_without_snapshot_event_join(
    tmp_path: Path,
) -> None:
    root = Path(f"/tmp/ksp-{os.getpid()}")
    shutil.rmtree(root, ignore_errors=True)
    workspace = WorkspaceApplication().init(root / "w", workspace_id="sp")
    (workspace.paths.root / "user_strategy.py").write_text(
        "from kairospy.strategy import Strategy\n"
        "class UserStrategy(Strategy):\n"
        "    strategy_id = 'process-strategy'\n",
        encoding="utf-8",
    )
    instance = workspace.instance("paper", "l", "i")
    instance.prepare()
    instance.component_manifest().write_text(
        '{"schema_version":1,"components":{},"accounts":{}}',
        encoding="utf-8",
    )
    process = StrategyProcessController(workspace, ready_timeout=5)
    socket = process.ensure_running(
        "user_strategy:UserStrategy",
        launch_id="l",
        instance_id="i",
    )
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
        process.stop("l", "i")
        shutil.rmtree(root, ignore_errors=True)


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
            calls.append(kwargs)
            return CommandResult(
                kwargs["request_id"],
                "accepted",
                {"removed_subscription_ids": ["subscription-1"]},
            )

    monkeypatch.setattr(
        "kairospy.application.market.composition.MarketCommandClient",
        lambda client, launch_id=None: Port(),
    )
    result = release_strategy_market_owner(workspace, instance)

    assert result is not None and result["status"] == "accepted"
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
    from kairospy.application.launch import LaunchRegistryApplication

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
    instance.component_manifest().write_text(
        '{"schema_version":1,"components":{"execution":{"socket":"%s"}},"accounts":{}}'
        % instance.socket("execution"),
        encoding="utf-8",
    )
    composition = compose_strategy_process(
        workspace,
        strategy_ref="user_strategy:UserStrategy",
        launch_id="launch",
        instance_id="run-1",
        mode="backtest",
    )
    assert composition.application.context.market._snapshots.path == (
        instance.snapshot("market", "market-shared")
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
    instance.component_manifest().write_text(
        '{"schema_version":1,"components":{},"accounts":{}}',
        encoding="utf-8",
    )

    composition = compose_strategy_process(
        workspace,
        strategy_ref="builtin:interactive",
        launch_id="manual",
        instance_id="run-1",
    )

    assert composition.entrypoint.strategy.strategy_id == "builtin-interactive"
    assert composition.application.context.account is not None
    assert composition.application.context.market._event_source.aeron_dir == str(
        workspace.paths.aeron_dir()
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
    from kairospy.strategy import InstrumentId

    disabled = composition.application.context.execution.target_position(
        InstrumentId("instrument:test:BTCUSDT"), Decimal("1"), account="main"
    )
    assert disabled.status == "rejected"
    assert disabled.error == "execution is disabled for this launch"


def test_authoritative_config_requires_enabled_execution_endpoint(
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
    instance.component_manifest().write_text(
        json.dumps({"schema_version": 1, "components": {}, "accounts": {}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Execution is enabled"):
        compose_strategy_process(
            workspace,
            strategy_ref="builtin:interactive",
            launch_id="launch",
            instance_id="run-1",
        )


def test_disabled_execution_ignores_a_residual_manifest_endpoint(
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
    instance.component_manifest().write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": {
                    "execution": {"socket": str(instance.socket("execution"))}
                },
                "accounts": {},
            }
        ),
        encoding="utf-8",
    )

    composition = compose_strategy_process(
        workspace,
        strategy_ref="builtin:interactive",
        launch_id="launch",
        instance_id="run-1",
    )

    assert composition.application.context.execution._commands is None
