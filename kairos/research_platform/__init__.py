"""Research platform services for studies, captures, validation, and workspaces."""

from __future__ import annotations

import importlib
import sys

from kairos.research_platform import (
    MarketDataType,
    OptionChainCaptureSpec,
    SMA_TUTORIAL_RELEASE_ID,
    StudyData,
    StudyProfile,
    StudySession,
    StudyWorkspace,
    StudyWorkspaceRepository,
    StudyWorkspaceStatus,
    ensure_sma_tutorial_dataset,
    open_study,
)


_ALIASED_MODULES = (
    "data_store",
    "features",
    "normalized_series",
    "option_capture",
    "option_snapshot_analysis",
    "option_universe_selector",
    "report",
    "retention",
    "series",
    "session",
    "snapshot",
    "spec",
    "tutorial_data",
    "validation",
    "workspace",
)


def _install_research_platform_aliases() -> None:
    for name in _ALIASED_MODULES:
        sys.modules.setdefault(f"{__name__}.{name}", importlib.import_module(f"kairos.research_platform.{name}"))


_install_research_platform_aliases()


__all__ = [
    "MarketDataType",
    "OptionChainCaptureSpec",
    "SMA_TUTORIAL_RELEASE_ID",
    "StudyData",
    "StudyProfile",
    "StudySession",
    "StudyWorkspace",
    "StudyWorkspaceRepository",
    "StudyWorkspaceStatus",
    "ensure_sma_tutorial_dataset",
    "open_study",
]
