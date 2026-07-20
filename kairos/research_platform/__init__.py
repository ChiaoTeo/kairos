"""Research platform services for studies, captures, validation, and workspaces."""

from __future__ import annotations

from pathlib import Path

from .session import StudyData, StudyProfile, StudySession, open_study
from .spec import MarketDataType, OptionChainCaptureSpec
from .workspace import StudyWorkspace, StudyWorkspaceRepository, StudyWorkspaceStatus


SMA_TUTORIAL_RELEASE_ID = "fixture:sma-bars-v1"


def ensure_sma_tutorial_dataset(root: str | Path):
    """Publish the bundled SMA fixture as a governed Dataset Release."""
    from .tutorial_data import ensure_sma_tutorial_dataset as ensure
    return ensure(root)


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
