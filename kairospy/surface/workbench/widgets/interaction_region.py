"""Passive renderer for the Workbench's current interaction state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..safety import redact_renderable
from kairospy.surface.presentation import redact_text
from .action_list import ActionItem
from .guided_action_list import GuidedActionList

if TYPE_CHECKING:
    from ..screens.operation import OperationSpec
    from ..screens.results import ResultRoute


class InteractionMode(Enum):
    """The five interaction mechanics supported by the Workbench."""

    CHOICE = "choice"
    INPUT = "input"
    CONFIRM = "confirm"
    RUNNING = "running"
    CONTROL = "control"


class Feature(StrEnum):
    """Closed owners of commands collected by the shared Input."""

    GLOBAL = "global"
    MARKET = "market"
    REFERENCE = "reference"
    STRATEGY = "strategy"
    RESOURCES = "resources"
    OPERATIONS = "operations"
    RESEARCH = "research"


@dataclass(frozen=True, slots=True)
class ActionToken:
    """Typed continuation for one product input step."""

    feature: Feature
    action: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ChoiceInteraction:
    """A context summary followed by the actions valid in that context."""

    title: str = ""
    summary: RenderableType | None = None
    actions: tuple[ActionItem, ...] = ()
    mode: InteractionMode = InteractionMode.CHOICE


@dataclass(frozen=True, slots=True)
class InputInteraction:
    """One field currently collected by the shared command input."""

    action: ActionToken
    title: str
    prompt: str
    detail: str
    value_summary: RenderableType | None = None
    secret: bool = False
    error: str | None = None
    mode: InteractionMode = InteractionMode.INPUT


@dataclass(frozen=True, slots=True)
class ConfirmInteraction:
    """A transient confirmation which has not yet entered the output history."""

    title: str
    summary: RenderableType
    operation: OperationSpec
    confirm_label: str = "确认"
    cancel_label: str = "取消"
    force_hint: str | None = None
    mode: InteractionMode = InteractionMode.CONFIRM


@dataclass(frozen=True, slots=True)
class RunningInteraction:
    """A finite asynchronous operation currently owning the shared input."""

    route: ResultRoute
    title: str
    message: str
    progress: float | None = None
    cancellable: bool = True
    mode: InteractionMode = InteractionMode.RUNNING


@dataclass(frozen=True, slots=True)
class ControlInteraction:
    """A live snapshot with controls that remain available while it refreshes."""

    title: str
    snapshot: RenderableType
    actions: tuple[ActionItem, ...] = ()
    refreshing: bool = False
    mode: InteractionMode = InteractionMode.CONTROL


InteractionState = (
    ChoiceInteraction
    | InputInteraction
    | ConfirmInteraction
    | RunningInteraction
    | ControlInteraction
)


def interaction_copy_text(interaction: InteractionState, *, width: int = 100) -> str:
    """Return safe text without exposing operations or input values."""

    output = StringIO()
    console = Console(
        file=output,
        width=width,
        color_system=None,
        force_terminal=False,
    )
    renderable = _interaction_renderable(interaction)
    if renderable is not None:
        console.print(renderable, markup=False, highlight=False)
    if isinstance(interaction, (ChoiceInteraction, ControlInteraction)):
        for item in interaction.actions:
            shortcut = f"[{item.shortcut}] " if item.shortcut else ""
            console.print(f"{shortcut}{item.label} — {item.description}")
    return redact_text(output.getvalue()).strip()


class InteractionRegion(Vertical):
    """Render interaction state without owning or interpreting it."""

    def __init__(
        self,
        initial_interaction: InteractionState | None = None,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        # This mount-time value is discarded immediately; the Session remains
        # the sole owner of the current interaction.
        self._initial_interaction = initial_interaction

    def compose(self) -> ComposeResult:
        yield Static(id="interaction-content")
        yield GuidedActionList(
            id="guided-actions",
            classes="action-cards guided-actions",
            spacious=False,
        )

    def on_mount(self) -> None:
        if self._initial_interaction is not None:
            self.present(self._initial_interaction)
            self._initial_interaction = None

    def present(self, interaction: InteractionState) -> None:
        """Replace the region atomically with one current interaction."""

        if not self.is_mounted:
            return
        content = self.query_one("#interaction-content", Static)
        actions = self.query_one("#guided-actions", GuidedActionList)
        items = _interaction_actions(interaction)
        actions.replace_items(items)
        actions.display = bool(items)
        renderable = _interaction_renderable(interaction)
        content.update(redact_renderable(renderable) if renderable is not None else "")
        content.display = renderable is not None


def _interaction_actions(interaction: InteractionState) -> tuple[ActionItem, ...]:
    if isinstance(interaction, (ChoiceInteraction, ControlInteraction)):
        return interaction.actions
    return ()


def _interaction_renderable(interaction: InteractionState) -> RenderableType | None:
    if isinstance(interaction, ChoiceInteraction):
        if interaction.summary is None:
            return None
        return Panel(interaction.summary, title=interaction.title or None)
    if isinstance(interaction, InputInteraction):
        body: list[RenderableType] = []
        if interaction.value_summary is not None:
            body.extend((interaction.value_summary, Text()))
        body.extend(
            (
                Text(interaction.prompt, style="bold"),
                Text(interaction.detail, style="dim"),
            )
        )
        if interaction.error:
            body.extend((Text(), Text(interaction.error, style="bold red")))
        return Panel(Group(*body), title=interaction.title, border_style="cyan")
    if isinstance(interaction, ConfirmInteraction):
        commands = Text()
        commands.append(f"[/confirm] {interaction.confirm_label}", style="bold yellow")
        commands.append("    ")
        commands.append(f"[/cancel] {interaction.cancel_label}", style="bold")
        parts: list[RenderableType] = [interaction.summary, Text(), commands]
        if interaction.force_hint:
            parts.append(Text(interaction.force_hint, style="dim"))
        return Panel(Group(*parts), title=interaction.title, border_style="yellow")
    if isinstance(interaction, RunningInteraction):
        detail = Text(interaction.message)
        if interaction.progress is not None:
            detail.append(f"\n进度 {interaction.progress:.0%}", style="cyan")
        if interaction.cancellable:
            detail.append("\nCtrl+C 取消当前任务", style="dim")
        return Panel(detail, title=interaction.title, border_style="cyan")
    state = "自动刷新中" if interaction.refreshing else "自动刷新已关闭"
    return Group(
        Text(interaction.title, style="bold cyan"),
        interaction.snapshot,
        Text(state, style="dim"),
    )


__all__ = [
    "ChoiceInteraction",
    "ConfirmInteraction",
    "ControlInteraction",
    "ActionToken",
    "Feature",
    "InputInteraction",
    "InteractionMode",
    "InteractionRegion",
    "InteractionState",
    "interaction_copy_text",
    "RunningInteraction",
]
