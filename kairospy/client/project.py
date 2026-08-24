"""Top-level public Kairos project client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from kairospy.system.apps.workspace.application import Workspace, WorkspaceApplication
from .data import DataClient

if TYPE_CHECKING:
    from kairospy.system.apps.launch.application.configuration import (
        LaunchConfig,
        LaunchPlan,
    )
    from kairospy.system.apps.launch.application.specs import BacktestResult, BacktestSpec
    from .research import ResearchClient


@dataclass(frozen=True, slots=True)
class Kairos:
    """Bound access to one explicit Project/.kairos context."""

    workspace: Workspace

    @classmethod
    def open(cls, project: str | Path) -> "Kairos":
        return cls(WorkspaceApplication().open(project))

    @property
    def data(self) -> DataClient:
        return DataClient(self.workspace)

    def launch_config(self, value: str | Path | LaunchConfig) -> LaunchConfig:
        """Load one canonical Launch Config from a value or Project config ID."""

        from kairospy.system.apps.launch.application.configuration import (
            LaunchConfig,
            LaunchConfigurationApplication,
        )

        if isinstance(value, LaunchConfig):
            return value
        candidate = Path(value).expanduser()
        path = (
            candidate.resolve()
            if candidate.is_file()
            else self.workspace.paths.launch_config(str(value))
        )
        return LaunchConfigurationApplication().load(
            path, workspace_root=self.workspace.paths.root
        )

    def launch_plan(self, value: str | Path | LaunchConfig) -> LaunchPlan:
        return self.launch_config(value).plan()

    def launch_instances(
        self, launch_id: str | None = None
    ) -> tuple[Mapping[str, Any], ...]:
        from kairospy.system.apps.launch.application.registry import (
            LaunchRegistryApplication,
        )

        registry = LaunchRegistryApplication(self.workspace)
        values = registry.instances(launch_id) if launch_id else registry.list()
        return tuple(dict(value) for value in values)

    async def start_launch(
        self,
        value: str | Path | LaunchConfig,
        *,
        instance_id: str | None = None,
        strategy_params: Mapping[str, Any] | None = None,
        account_ids: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        from kairospy.system.apps.launch.application.runtime import (
            LaunchRuntimeApplication,
        )

        return await asyncio.to_thread(
            LaunchRuntimeApplication(self.workspace).start,
            self.launch_config(value),
            instance_id=instance_id,
            strategy_params=strategy_params,
            account_ids=account_ids,
        )

    def launch_status(
        self, launch_id: str, *, instance: str | None = None
    ) -> Mapping[str, Any]:
        from kairospy.system.apps.launch.application.runtime import (
            LaunchRuntimeApplication,
        )

        return LaunchRuntimeApplication(self.workspace).status(
            launch_id, instance=instance
        )

    async def wait_launch(
        self,
        launch_id: str,
        *,
        instance: str | None = None,
        timeout: float = 3600.0,
    ) -> Mapping[str, Any]:
        from kairospy.system.apps.launch.application.runtime import (
            LaunchRuntimeApplication,
        )

        return await asyncio.to_thread(
            LaunchRuntimeApplication(self.workspace).wait,
            launch_id,
            instance=instance,
            timeout=timeout,
        )

    def launch_report(
        self, launch_id: str, *, instance: str | None = None
    ) -> Mapping[str, Any]:
        from kairospy.system.apps.launch.application.runtime import (
            LaunchRuntimeApplication,
        )

        return LaunchRuntimeApplication(self.workspace).report(
            launch_id, instance=instance
        )

    async def stop_launch(
        self,
        launch_id: str,
        *,
        instance: str | None = None,
        mode: str | None = None,
    ) -> Mapping[str, Any]:
        from kairospy.system.apps.launch.application.runtime import (
            LaunchRuntimeApplication,
        )

        return await asyncio.to_thread(
            LaunchRuntimeApplication(self.workspace).stop,
            launch_id,
            instance=instance,
            mode=mode,
        )

    async def run_backtest(
        self,
        spec: BacktestSpec,
        *,
        timeout: float = 3600.0,
    ) -> BacktestResult:
        """Run a typed Backtest request through canonical Launch + Config."""

        from kairospy.system.apps.launch.application.backtests import BacktestApplication

        return await BacktestApplication(self.workspace).run(spec, timeout=timeout)

    @property
    def research(self) -> ResearchClient:
        from .research import ResearchClient

        return ResearchClient(self.workspace)


__all__ = ["Kairos"]
