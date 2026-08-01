from __future__ import annotations

import json
from typing import Mapping

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static, TextArea


class JsonDetail(Vertical):
    """Bottom detail and JSON editor panel for resource rows."""

    def compose(self) -> ComposeResult:
        yield Label("Details", classes="panel-title")
        yield Static("Select a row to inspect it.", id="detail")
        yield TextArea(id="editor", language="json")

    @property
    def detail(self) -> Static:
        return self.query_one("#detail", Static)

    @property
    def editor(self) -> TextArea:
        return self.query_one("#editor", TextArea)

    def show_payload(self, payload: object) -> None:
        self.detail.update(json_renderable(payload))
        self.styles.display = "block"

    def show_message(self, message: str) -> None:
        self.detail.update(message)
        self.styles.display = "block"

    def start_editing(self, row: Mapping[str, object]) -> None:
        self.editor.text = json.dumps(dict(row), ensure_ascii=False, indent=2)
        self.editor.styles.display = "block"
        self.styles.display = "block"
        self.editor.focus()

    def stop_editing(self) -> None:
        self.editor.styles.display = "none"

    def hide(self) -> None:
        self.styles.display = "none"
        self.stop_editing()


def json_renderable(value: object) -> Syntax:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    return Syntax(text, "json", word_wrap=True, background_color="default")


__all__ = ["JsonDetail", "json_renderable"]
