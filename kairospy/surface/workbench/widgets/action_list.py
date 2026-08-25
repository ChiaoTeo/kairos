"""Consistent keyboard-first action lists."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True, slots=True)
class ActionItem:
    id: str
    label: str
    description: str
    shortcut: str | None = None
    disabled: bool = False
    spacious: bool = False


class ActionList(OptionList):
    """An OptionList whose visual hierarchy is shared by every task screen."""

    def __init__(
        self,
        *items: ActionItem,
        id: str | None = None,
        classes: str | None = None,
        spacious: bool = False,
    ) -> None:
        self.items = items
        super().__init__(
            *(
                Option(
                    _action_prompt(item, spacious=spacious or item.spacious),
                    id=item.id,
                    disabled=item.disabled,
                )
                for item in items
            ),
            id=id,
            classes=classes,
        )

    def highlight_shortcut(self, shortcut: str) -> bool:
        for index, item in enumerate(self.items):
            if item.shortcut == shortcut and not item.disabled:
                self.highlighted = index
                return True
        return False


def _action_prompt(item: ActionItem, *, spacious: bool = False) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(f"[{item.shortcut}]  ", style="bold cyan")
    prompt.append(item.label, style="bold")
    if spacious:
        prompt.append(f"\n     {item.description}", style="dim")
    else:
        prompt.append(f"  ·  {item.description}", style="dim")
    return prompt
