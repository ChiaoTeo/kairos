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
from ..theme import NORD_COLORS, RichThemeColors, rich_theme_colors
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
    ACCOUNT = "account"
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
class InteractionHeading:
    """One compact object description above the current actions."""

    title: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ChoiceInteraction:
    """A context summary followed by the actions valid in that context."""

    title: str = ""
    summary: RenderableType | None = None
    actions: tuple[ActionItem, ...] = ()
    heading: InteractionHeading | None = None
    state: str | None = None
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
    heading: InteractionHeading | None = None
    state: str | None = None
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
    for item in _interaction_actions(interaction):
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
        self._current_interaction: InteractionState | None = initial_interaction

    def compose(self) -> ComposeResult:
        yield Static(id="interaction-content")
        yield GuidedActionList(
            id="guided-actions",
            classes="action-cards guided-actions",
            spacious=False,
        )

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)
        if self._initial_interaction is not None:
            self.present(self._initial_interaction)
            self._initial_interaction = None

    def _theme_changed(self, _theme: object) -> None:
        if self._current_interaction is not None:
            self.present(self._current_interaction)

    def present(self, interaction: InteractionState) -> None:
        """Replace the region atomically with one current interaction."""

        if not self.is_mounted:
            self._current_interaction = interaction
            return
        self._current_interaction = interaction
        content = self.query_one("#interaction-content", Static)
        actions = self.query_one("#guided-actions", GuidedActionList)
        items = _interaction_actions(interaction)
        actions.replace_items(items)
        actions.display = bool(items)
        content.set_class(
            isinstance(interaction, (ChoiceInteraction, InputInteraction)),
            "compact-interaction-content",
        )
        renderable = _interaction_renderable(
            interaction, colors=rich_theme_colors(self.app.current_theme)
        )
        content.update(redact_renderable(renderable) if renderable is not None else "")
        content.display = renderable is not None


def _interaction_actions(interaction: InteractionState) -> tuple[ActionItem, ...]:
    if isinstance(interaction, (ChoiceInteraction, ControlInteraction)):
        return interaction.actions
    if isinstance(interaction, ConfirmInteraction):
        return (
            ActionItem(
                id="confirm:cancel",
                label=interaction.cancel_label,
                description="返回，不执行操作",
                shortcut="n",
            ),
            ActionItem(
                id="confirm:accept",
                label=interaction.confirm_label,
                description="执行当前操作",
                shortcut="y",
            ),
        )
    return ()


def _interaction_renderable(
    interaction: InteractionState, *, colors: RichThemeColors = NORD_COLORS
) -> RenderableType | None:
    if isinstance(interaction, ChoiceInteraction):
        if interaction.heading is not None:
            heading = _heading_renderable(interaction.heading, colors=colors)
            return (
                Group(heading, interaction.summary)
                if interaction.summary is not None
                else heading
            )
        if interaction.summary is None:
            return None
        if interaction.title:
            return Group(
                Text(interaction.title, style=f"bold {colors.primary}"),
                interaction.summary,
            )
        return interaction.summary
    if isinstance(interaction, InputInteraction):
        if interaction.heading is not None:
            body: list[RenderableType] = [
                _heading_renderable(interaction.heading, colors=colors)
            ]
            if interaction.value_summary is not None:
                body.append(interaction.value_summary)
            body.append(Text(interaction.prompt, style="bold"))
            if interaction.detail:
                body.append(Text(interaction.detail, style=colors.muted))
            if interaction.error:
                body.append(Text(interaction.error, style=f"bold {colors.error}"))
            return Group(*body)
        body = []
        if interaction.value_summary is not None:
            body.append(interaction.value_summary)
        prompt = Text(interaction.prompt, style="bold")
        if interaction.detail:
            prompt.append("  ·  ", style=colors.muted)
            prompt.append(interaction.detail, style=colors.muted)
        body.append(prompt)
        if interaction.error:
            body.append(Text(interaction.error, style=f"bold {colors.error}"))
        return Group(*body)
    if isinstance(interaction, ConfirmInteraction):
        parts: list[RenderableType] = [interaction.summary]
        if interaction.force_hint:
            parts.extend((Text(), Text(interaction.force_hint, style=colors.muted)))
        return Panel(
            Group(*parts), title=interaction.title, border_style=colors.warning
        )
    if isinstance(interaction, RunningInteraction):
        detail = Text(interaction.message)
        if interaction.progress is not None:
            detail.append(f"\n进度 {interaction.progress:.0%}", style=colors.primary)
        if interaction.cancellable:
            detail.append("\nCtrl+C 取消当前任务", style=colors.muted)
        return Panel(detail, title=interaction.title, border_style=colors.primary)
    state = "自动刷新中" if interaction.refreshing else "自动刷新已关闭"
    return Group(
        Text(interaction.title, style=f"bold {colors.primary}"),
        interaction.snapshot,
        Text(state, style=colors.muted),
    )


def _heading_renderable(
    heading: InteractionHeading, *, colors: RichThemeColors
) -> Text:
    value = Text(heading.title, style=f"bold {colors.primary}")
    if heading.detail:
        value.append("  ·  ", style=colors.muted)
        value.append(heading.detail, style=colors.muted)
    return value


__all__ = [
    "ChoiceInteraction",
    "ConfirmInteraction",
    "ControlInteraction",
    "ActionToken",
    "Feature",
    "InputInteraction",
    "InteractionMode",
    "InteractionHeading",
    "InteractionRegion",
    "InteractionState",
    "interaction_copy_text",
    "RunningInteraction",
]
