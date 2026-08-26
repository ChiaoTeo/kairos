"""A keyboard-selectable current-action list beside the command input."""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text
from textual.events import MouseDown, Resize
from textual.widgets.option_list import Option

from ..theme import PRIMARY, rich_theme_foreground, rich_theme_muted
from .action_list import ActionItem, ActionList


class GuidedActionList(ActionList):
    """Render actions that may be selected directly or by typed shortcut."""

    _mouse_selection_pending = False

    def on_mouse_down(self, event: MouseDown) -> None:
        """Highlight a mouse target and mark its click as selection-only."""

        clicked_option = event.style.meta.get("option")
        if not isinstance(clicked_option, int):
            return
        option = self.get_option_at_index(clicked_option)
        if option.disabled:
            return
        self.focus()
        self.highlighted = clicked_option
        self._mouse_selection_pending = True
        event.stop()

    def action_select(self) -> None:
        """Require Enter after a mouse click has highlighted an option."""

        if self._mouse_selection_pending:
            self._mouse_selection_pending = False
            return
        super().action_select()

    def on_focus(self) -> None:
        """Give keyboard navigation a deterministic starting point."""

        if self.highlighted is None:
            self.action_first()

    def on_blur(self) -> None:
        """Remove the selection marker when input owns the keyboard again."""

        self.highlighted = None

    def replace_items(self, items: tuple[ActionItem, ...]) -> None:
        self.items = items
        self._replace_options()

    def refresh_theme(self) -> None:
        self._replace_options()

    def on_resize(self, event: Resize) -> None:
        compact = event.size.width < 68
        if compact != getattr(self, "_compact", None):
            self._compact = compact
            self._replace_options()

    def _replace_options(self) -> None:
        compact = getattr(self, "_compact", self.size.width < 68)
        muted = rich_theme_muted(self.app.current_theme)
        foreground = rich_theme_foreground(self.app.current_theme)
        heading_width = max(
            (_action_heading_cell_width(item) for item in self.items), default=0
        )
        # Explicitly clear the OptionList before adding the next context.  This
        # also resets its virtual height, preventing shorter prior menus from
        # remaining painted below the current service actions.
        self.clear_options()
        self.add_options(
            Option(
                (
                    _compact_prompt(
                        item,
                        primary=self.app.current_theme.primary,
                        foreground=foreground,
                    )
                    if compact
                    else _guided_action_prompt(
                        item,
                        heading_width=heading_width,
                        primary=self.app.current_theme.primary,
                        foreground=foreground,
                        muted=muted,
                    )
                ),
                id=item.id,
                disabled=item.disabled,
            )
            for item in self.items
        )
        self.refresh(layout=True)


def _compact_prompt(
    item: ActionItem,
    *,
    primary: str = PRIMARY,
    foreground: str = "#eceff4",
) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(
            f"[{_display_shortcut(item.shortcut)}]  ", style=f"bold {primary}"
        )
    prompt.append(item.label, style=f"bold {foreground}")
    return prompt


def _guided_action_prompt(
    item: ActionItem,
    *,
    heading_width: int,
    primary: str = PRIMARY,
    foreground: str = "#eceff4",
    muted: str = "#d8dee9",
) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(
            f"[{_display_shortcut(item.shortcut)}]  ", style=f"bold {primary}"
        )
    prompt.append(item.label, style=f"bold {foreground}")
    if item.spacious:
        prompt.append(f"\n     {item.description}", style=muted)
    else:
        prompt.append(" " * (heading_width - _action_heading_cell_width(item)))
        prompt.append(f"  ·  {item.description}", style=muted)
    return prompt


def _action_heading_cell_width(item: ActionItem) -> int:
    shortcut = f"[{_display_shortcut(item.shortcut)}]  " if item.shortcut else ""
    return cell_len(shortcut) + cell_len(item.label)


def _display_shortcut(value: str) -> str:
    return value if value.isdecimal() else f"/{value}"


__all__ = ["GuidedActionList"]
