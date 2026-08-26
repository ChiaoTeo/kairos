"""Keyboard-first input for the guided Workbench command line."""

from __future__ import annotations

from textual.binding import Binding
from textual.suggester import SuggestFromList
from textual.widgets import Input


COMMAND_SUGGESTIONS = (
    "help",
    "panel ",
    "clear",
    "observe",
    "market ",
    "y",
    "n",
)


class WorkbenchCommandInput(Input):
    """Command input with shell-style history and completion suggestions."""

    BINDINGS = [
        Binding("up", "history_previous", show=False),
        Binding("down", "history_next", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            placeholder="输入命令；Enter 提交",
            suggester=SuggestFromList(COMMAND_SUGGESTIONS, case_sensitive=False),
            id=id,
        )
        self._history: list[str] = []
        self._history_index = 0

    def remember(self, value: str) -> None:
        value = value.strip()
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)
        self._history_index = len(self._history)

    def action_history_previous(self) -> None:
        if not self._history:
            return
        self._history_index = max(0, self._history_index - 1)
        self._replace(self._history[self._history_index])

    def action_history_next(self) -> None:
        if not self._history:
            return
        self._history_index = min(len(self._history), self._history_index + 1)
        value = (
            ""
            if self._history_index == len(self._history)
            else self._history[self._history_index]
        )
        self._replace(value)

    def _replace(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)
