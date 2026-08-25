"""Consistent keyboard-first action lists."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..theme import PRIMARY, rich_theme_foreground, rich_theme_muted


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
        self._spacious = spacious
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

    def on_mount(self) -> None:
        self.app.theme_changed_signal.subscribe(self, self._theme_changed)
        self.refresh_theme()

    def _theme_changed(self, _theme: object) -> None:
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """Rebuild Rich prompts with the active theme's primary color."""

        highlighted = self.highlighted
        self.clear_options()
        self.add_options(
            Option(
                _action_prompt(
                    item,
                    spacious=self._spacious or item.spacious,
                    primary=self.app.current_theme.primary,
                    foreground=rich_theme_foreground(self.app.current_theme),
                    muted=rich_theme_muted(self.app.current_theme),
                ),
                id=item.id,
                disabled=item.disabled,
            )
            for item in self.items
        )
        self.highlighted = highlighted

    def highlight_shortcut(self, shortcut: str) -> bool:
        for index, item in enumerate(self.items):
            if item.shortcut == shortcut and not item.disabled:
                self.highlighted = index
                return True
        return False


def _action_prompt(
    item: ActionItem,
    *,
    spacious: bool = False,
    primary: str = PRIMARY,
    foreground: str = "#eceff4",
    muted: str = "#9aa3b2",
) -> Text:
    prompt = Text()
    if item.shortcut:
        prompt.append(f"[{item.shortcut}]  ", style=f"bold {PRIMARY}")
    prompt.append(item.label, style=f"bold {foreground}")
    if spacious:
        prompt.append(f"\n     {item.description}", style=muted)
    else:
        prompt.append(f"  ·  {item.description}", style=muted)
    return prompt
