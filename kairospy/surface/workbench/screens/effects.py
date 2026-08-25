"""Closed presentation effects produced by Workbench product flows."""

from __future__ import annotations

from dataclasses import dataclass

from ..widgets import InteractionState
from .activity import ActivityRecord
from .operation import OperationSpec


@dataclass(frozen=True, slots=True)
class AppendActivity:
    activity: ActivityRecord


@dataclass(frozen=True, slots=True)
class SetInteraction:
    interaction: InteractionState


@dataclass(frozen=True, slots=True)
class RunOperation:
    operation: OperationSpec


@dataclass(frozen=True, slots=True)
class SetStatus:
    message: str


@dataclass(frozen=True, slots=True)
class RefreshMarketControl:
    """Ask the Textual adapter to schedule one live Market refresh."""

    force: bool = True


@dataclass(frozen=True, slots=True)
class RefreshLaunchControl:
    """Ask the Textual adapter to render and refresh the Launch live control."""

    force: bool = True


@dataclass(frozen=True, slots=True)
class RefreshOperationsLogs:
    """Ask the Textual adapter to refresh a service's transient log stream."""

    force: bool = True
    reset_view: bool = False


ScreenEffect = (
    AppendActivity
    | SetInteraction
    | RunOperation
    | SetStatus
    | RefreshMarketControl
    | RefreshLaunchControl
    | RefreshOperationsLogs
)


__all__ = [
    "AppendActivity",
    "RefreshMarketControl",
    "RefreshLaunchControl",
    "RefreshOperationsLogs",
    "RunOperation",
    "ScreenEffect",
    "SetInteraction",
    "SetStatus",
]
