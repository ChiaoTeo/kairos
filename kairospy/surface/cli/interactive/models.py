"""State and command models shared by the interactive CLI surface."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from kairospy.surface.console.models import ObserveSnapshot


ExecuteCommand = Callable[[Sequence[str]], int]


class ShellControl(Enum):
    """Control result returned by a section after handling input locally."""

    HANDLED = "handled"


class CommandExecution(Enum):
    """How a guided command owns terminal input and output."""

    ACTIVITY = "activity"
    INTERACTIVE = "interactive"
    STREAMING = "streaming"


@dataclass(frozen=True, slots=True)
class GuidedCommand:
    """One existing CLI command selected through the interactive surface."""

    argv: tuple[str, ...]
    summary: str
    dangerous: bool = False
    confirmation: str | None = None
    needs_workspace: bool = True
    execution: CommandExecution = CommandExecution.ACTIVITY
    show_command: bool = True


ShellAction = GuidedCommand | ShellControl | None


@dataclass(slots=True)
class InteractiveContext:
    """Mutable navigation context for one interactive CLI session."""

    owner: Any | None
    snapshot: ObserveSnapshot | None
    workspace_arg: Path | None
    selected_launch: str | None = None
    selected_launch_mode: str | None = None
    selected_launch_instance: str | None = None
    selected_account: str | None = None
    selected_account_provider: str | None = None
    selected_account_environment: str | None = None
    selected_account_segment: str | None = None
    selected_order: str | None = None
    selected_order_symbol: str | None = None
    selected_service: str | None = None
    selected_market: Any | None = None
    selected_market_provider: dict[str, Any] | None = None
    selected_reference: Any | None = None
    selected_reference_kind: str | None = None
    last_command: str | None = None
    last_status: int | None = None
    shell_path: tuple[str, ...] = ()


__all__ = [
    "ExecuteCommand",
    "CommandExecution",
    "GuidedCommand",
    "InteractiveContext",
    "ShellAction",
    "ShellControl",
]
