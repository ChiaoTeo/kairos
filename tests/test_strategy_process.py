from __future__ import annotations

import asyncio
import os
import shutil
import json
from pathlib import Path

from kairospy.application.strategy import StrategyProcessApplication
from kairospy.application.strategy.services.composition import compose_strategy_process
from kairospy.application.launch import LaunchControlApplication
from kairospy.application.system import UnixRestClient
from kairospy.application.workspace import WorkspaceApplication
from kairospy.strategy import StrategyCommand
from kairospy.strategy import CommandResult


def test_strategy_process_is_started_per_launch_instance_and_reports_waiting_snapshot(
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
        '{"schema_version":1,"components":{"execution":{"socket":"%s"}},"accounts":{}}'
        % instance.socket("execution"),
        encoding="utf-8",
    )
    process = StrategyProcessApplication(workspace, ready_timeout=5)
    socket = process.ensure_running(
        "user_strategy:UserStrategy",
        launch_id="l",
        instance_id="i",
    )
    assert workspace.instance("paper", "l", "i").log("strategy.log").is_file()
    assert not (
        workspace.paths.logs / "launches" / "paper" / "l" / "i" / "strategy.log"
    ).exists()
    try:
        started = asyncio.run(UnixRestClient(socket).request("POST", "/v1/start"))
        assert started["status"] == "waiting_for_dependencies"
        assert "snapshot pending" in started["reason"]
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


def test_external_stop_releases_subscriptions_for_dead_strategy_process(
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
    (instance.root / "lifecycle.jsonl").write_text(
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
        "kairospy.infrastructure.transport.commands.MarketCommandClient",
        lambda client, launch_id=None: Port(),
    )
    result = StrategyProcessApplication(workspace)._release_orphaned_subscriptions(
        "launch", "instance", "paper"
    )

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
    assert composition.host.stream.socket_path == workspace.paths.instance_socket(
        "backtest", "launch", "run-1", "market-events"
    )
    assert composition.host._snapshots.path == workspace.paths.instance_snapshot(
        "backtest", "launch", "run-1", "market", "market.snapshot"
    )
    assert (
        composition.host.clients.execution_commands.client.socket_path
        == workspace.paths.instance_socket("backtest", "launch", "run-1", "execution")
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
    assert composition.host.context.account is not None
    composition.host.start()
    result = asyncio.run(
        composition.host.command(
            StrategyCommand("command-1", "interactive.python", "1 + 1")
        )
    )
    assert result.status == "completed"
    assert result.result["value"] == 2
    from decimal import Decimal
    from kairospy.strategy import InstrumentId

    disabled = composition.host.context.execution.target_position(
        InstrumentId("instrument:test:BTCUSDT"), Decimal("1"), account="main"
    )
    assert disabled.status == "rejected"
    assert disabled.error == "execution is disabled for this launch"
