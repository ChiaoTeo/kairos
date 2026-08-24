"""Single-value input dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class InputDialog(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(
        self,
        title: str,
        *,
        placeholder: str = "",
        value: str = "",
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.placeholder = placeholder
        self.initial_value = value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield Input(
                value=self.initial_value,
                placeholder=self.placeholder,
                id="dialog-input",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)
