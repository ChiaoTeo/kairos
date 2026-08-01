from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.support.launch.host.lifecycle import TradingLifecycle
from kairospy.application.support.launch.host.resources import TradingRuntimeResources
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.runtime.launch import RuntimeLaunchResult
from kairospy.application.support.runtime.services.reference import ReferenceCatalogService
from kairospy.application.support.system.workspace import KairosWorkspace
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.infrastructure.persistence.reference.sqlite_store import SqliteReferenceStore


class RuntimeLauncher(Protocol):
    def __call__(
        self,
        *,
        launch_id: str,
        mode: RuntimeMode,
        strategy: Strategy,
        launch_directory: Path,
        normalized_config: Mapping[str, object],
        resources: TradingRuntimeResources,
        lifecycle: TradingLifecycle | None,
    ) -> RuntimeLaunchResult:
        pass


def reference_runtime(
    start: str | Path | None,
    *,
    default_venue: str | None = None,
    default_market: str | None = None,
) -> ReferenceCatalogService:
    workspace = KairosWorkspace.resolve(start)
    return ReferenceCatalogService(
        SqliteReferenceStore(workspace.reference_root),
        default_venue=optional_default_text(default_venue),
        default_market=optional_default_text(default_market),
    )


def optional_default_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


__all__ = ["RuntimeLauncher", "optional_default_text", "reference_runtime"]
