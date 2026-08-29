"""Keyboard-first input for the guided Workbench command line."""

from __future__ import annotations

from textual.binding import Binding
from textual.suggester import SuggestFromList
from textual.widgets import Input, OptionList


COMMAND_SUGGESTIONS = ("?",)


class WorkbenchCommandInput(Input):
    """Command input with shell-style history and completion suggestions."""

    BINDINGS = [
        Binding("up", "history_previous", show=False),
        Binding("down", "history_next", show=False),
        Binding("ctrl+up", "history_previous_value", show=False),
        Binding("ctrl+down", "history_next_value", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            placeholder="输入编号，或按 ↑↓ 选择；Enter 确认",
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
        if not self.value and self._focus_actions(last=True):
            return
        self.action_history_previous_value()

    def action_history_previous_value(self) -> None:
        if not self._history:
            return
        self._history_index = max(0, self._history_index - 1)
        self._replace(self._history[self._history_index])

    def action_history_next(self) -> None:
        if not self.value and self._history_index == len(self._history):
            if self._focus_actions(last=False):
                return
        self.action_history_next_value()

    def action_history_next_value(self) -> None:
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

    def _focus_actions(self, *, last: bool) -> bool:
        """Let an empty navigation input hand arrow control to visible choices."""

        actions = self.screen.query_one("#guided-actions", OptionList)
        if not actions.display or not actions.option_count:
            return False
        self.app.set_focus(actions)
        actions.highlighted = actions.option_count - 1 if last else 0
        return True
