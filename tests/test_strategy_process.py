from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from kairospy.application.strategy import StrategyProcessApplication
from kairospy.application.launch import LaunchControlApplication
from kairospy.application.system import UnixRestClient
from kairospy.application.workspace import WorkspaceApplication


def test_strategy_process_is_started_per_launch_instance_and_reports_waiting_snapshot(tmp_path: Path) -> None:
    root = Path(f"/tmp/ksp-{os.getpid()}")
    shutil.rmtree(root, ignore_errors=True)
    workspace = WorkspaceApplication().init(root / "w", workspace_id="sp")
    (workspace.paths.root / "user_strategy.py").write_text(
        "from kairospy.strategy import StrategyBase\n"
        "class UserStrategy(StrategyBase):\n"
        "    strategy_id = 'process-strategy'\n",
        encoding="utf-8",
    )
    process = StrategyProcessApplication(workspace, ready_timeout=5)
    socket = process.ensure_running(
        "user_strategy:UserStrategy",
        launch_id="l",
        instance_id="i",
    )
    try:
        started = asyncio.run(UnixRestClient(socket).request("POST", "/v1/start"))
        assert started["status"] == "waiting_for_dependencies"
        assert "snapshot pending" in started["reason"]
    finally:
        process.stop("l", "i")
        shutil.rmtree(root, ignore_errors=True)


def test_launch_status_and_stop_are_safe_when_instance_is_not_running(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="sp-status")
    application = LaunchControlApplication(workspace)
    target = application.target("launch", "instance")
    assert application.status(target)["status"] == "not_running"
    assert application.stop(target)["status"] == "not_running"
