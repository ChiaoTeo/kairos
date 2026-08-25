"""A current-action display that never competes with the command input."""

from __future__ import annotations

from rich.text import Text
from textual.events import Resize
from textual.widgets.option_list import Option

from .action_list import ActionItem, ActionList


class GuidedActionList(ActionList):
    """Render HomeScreen-style actions while leaving input ownership below."""

    can_focus = False
    can_focus_children = False

    def replace_items(self, items: tuple[ActionItem, ...]) -> None:
        self.items = items
        self._replace_options()

    def on_resize(self, event: Resize) -> None:
        compact = event.size.width < 68
        if compact != getattr(self, "_compact", None):
            self._compact = compact
            self._replace_options()

    def _replace_options(self) -> None:
        compact = getattr(self, "_compact", self.size.width < 68)
        # Explicitly clear the OptionList before adding the next context.  This
        # also resets its virtual height, preventing shorter prior menus from
        # remaining painted below the current service actions.
        self.clear_options()
        self.add_options(
            Option(
                _compact_prompt(item) if compact else _guided_action_prompt(item),
                id=item.id,
            )
            for item in self.items
        )
        self.refresh(layout=True)


def _compact_prompt(item: ActionItem) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(f"[{_display_shortcut(item.shortcut)}]  ", style="bold cyan")
    prompt.append(item.label, style="bold")
    return prompt


def _guided_action_prompt(item: ActionItem) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(f"[{_display_shortcut(item.shortcut)}]  ", style="bold cyan")
    prompt.append(item.label, style="bold")
    if item.spacious:
        prompt.append(f"\n     {item.description}", style="dim")
    else:
        prompt.append(f"  ·  {item.description}", style="dim")
    return prompt


def _display_shortcut(value: str) -> str:
    return value if value.isdecimal() else f"/{value}"


__all__ = ["GuidedActionList"]
