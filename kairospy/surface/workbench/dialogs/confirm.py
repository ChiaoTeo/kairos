"""Dangerous-action confirmation dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(
        self, title: str, message: str, *, confirm_label: str = "确认"
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield Label(self.message, classes="dialog-message")
            with Horizontal(classes="dialog-actions"):
                yield Button("取消", id="cancel")
                yield Button(self.confirm_label, variant="warning", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)
