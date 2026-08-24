"""Single-choice dialog backed by OptionList."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True, slots=True)
class SelectOption:
    id: str
    label: str
    disabled: bool = False


class SelectDialog(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, title: str, options: tuple[SelectOption, ...]) -> None:
        super().__init__()
        self.dialog_title = title
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield OptionList(
                *(
                    Option(option.label, id=option.id, disabled=option.disabled)
                    for option in self.options
                ),
                id="dialog-options",
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
