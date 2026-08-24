"""Typed backtest use cases over the canonical Launch runtime."""

from __future__ import annotations

import asyncio

from kairospy.system.apps.workspace.application import Workspace
from .runtime import LaunchRuntimeApplication
from .specs import BacktestResult, BacktestSpec


class BacktestApplication:
    """Run typed backtests without introducing a second runtime."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def run(
        self,
        spec: BacktestSpec,
        *,
        timeout: float = 3600.0,
    ) -> BacktestResult:
        config = spec.to_launch_config(workspace_root=self.workspace.paths.root)
        runtime = LaunchRuntimeApplication(self.workspace)
        started = await asyncio.to_thread(runtime.start, config)
        instance_id = str(started["instance_id"])
        completed = await asyncio.to_thread(
            runtime.wait,
            config.launch_id,
            instance=instance_id,
            timeout=timeout,
        )
        report = completed.get("report")
        return BacktestResult(
            launch_id=config.launch_id,
            instance_id=instance_id,
            status=str(completed.get("status") or "failed"),
            normalized_config_hash=config.normalized_hash,
            report=report if isinstance(report, dict) else {},
        )


__all__ = ["BacktestApplication"]
