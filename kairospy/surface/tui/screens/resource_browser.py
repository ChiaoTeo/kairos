from __future__ import annotations

import json
from typing import Mapping

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label

from kairospy.application.support.query.browsing import ListQuery, query_rows
from kairospy.surface.tui.models import ResourceList
from kairospy.surface.tui.widgets import JsonDetail, QueryBar, ResourceTable, query_summary


class ResourceBrowserScreen(Screen[None]):
    """Reusable table-first screen for Kairos resource lists."""

    BINDINGS = [
        Binding("slash", "focus_search", "Search", show=True),
        Binding("f", "focus_filter", "Filter", show=True),
        Binding("s", "focus_sort", "Sort", show=True),
        Binding("g", "focus_page", "Go to page", show=True),
        Binding("n", "next_page", "Next", show=True),
        Binding("p", "previous_page", "Previous", show=True),
        Binding("e", "edit_row", "Edit", show=True),
        Binding("ctrl+s", "save_row", "Save", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, resource: ResourceList, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.resource = resource
        self.list_query = resource.query or ListQuery()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="command-bar"):
            yield Label("", id="summary")
            yield QueryBar(self.list_query)
        with Vertical(id="body"):
            with Vertical(id="table-panel"):
                yield Label("Rows", classes="panel-title")
                yield ResourceTable()
            yield JsonDetail(id="detail-panel")
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.table = self.query_one(ResourceTable)
        self.detail_panel = self.query_one(JsonDetail)
        self.query_bar = self.query_one(QueryBar)
        self.status = self.query_one("#status", Label)
        self.summary = self.query_one("#summary", Label)
        self._refresh("Ready")
        self.table.focus()

    def _refresh(self, message: str = "Updated") -> None:
        result = query_rows(self.resource.rows, self.list_query, columns=self.resource.columns)
        self.table.replace_rows(result.columns, result.rows)
        self.summary.update(query_summary(self.list_query))
        self.status.update(f"Page {result.page}/{result.total_pages} | {result.total_rows} rows | {message}")
        if not result.rows:
            self.detail_panel.show_message("No rows match the current query.")

    def _set_query_from_inputs(self, *, reset_page: bool = True) -> None:
        self.list_query = self.query_bar.read_query(self.list_query, reset_page=reset_page)
        self.resource.query = self.list_query

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            self._set_query_from_inputs(reset_page=event.input.id != "page")
            self._refresh("Query applied")
        except (TypeError, ValueError) as error:
            self.status.update(f"error: {error}")

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_focus_sort(self) -> None:
        self.query_one("#sort", Input).focus()

    def action_focus_page(self) -> None:
        self.query_one("#page", Input).focus()

    def action_next_page(self) -> None:
        if isinstance(self.focused, Input):
            return
        result = query_rows(self.resource.rows, self.list_query, columns=self.resource.columns)
        self.list_query = replace_query(self.list_query, page=min(result.total_pages, self.list_query.page + 1))
        self.resource.query = self.list_query
        self.query_bar.set_page(self.list_query.page)
        self._refresh("Next page")

    def action_previous_page(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.list_query = replace_query(self.list_query, page=max(1, self.list_query.page - 1))
        self.resource.query = self.list_query
        self.query_bar.set_page(self.list_query.page)
        self._refresh("Previous page")

    def on_data_table_row_selected(self, event: ResourceTable.RowSelected) -> None:
        result = query_rows(self.resource.rows, self.list_query, columns=self.resource.columns)
        if event.cursor_row >= len(result.rows):
            return
        row = result.rows[event.cursor_row]
        payload = self.resource.detail(row) if self.resource.detail else row
        self.detail_panel.show_payload(payload)

    def action_edit_row(self) -> None:
        result = query_rows(self.resource.rows, self.list_query, columns=self.resource.columns)
        if not result.rows:
            self.status.update("No row selected")
            return
        if self.resource.save is None:
            self.status.update("This resource is read-only")
            return
        index = min(self.table.cursor_row, len(result.rows) - 1)
        self.detail_panel.start_editing(result.rows[index])
        self.status.update("Edit JSON, Ctrl+S to save, Esc to cancel")

    def action_save_row(self) -> None:
        if self.resource.save is None or self.detail_panel.editor.styles.display == "none":
            self.status.update("Nothing to save")
            return
        try:
            payload = json.loads(self.detail_panel.editor.text)
            if not isinstance(payload, Mapping):
                raise ValueError("edited value must be a JSON object")
            self.resource.save(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.status.update(f"error: {error}")
            return
        self.detail_panel.stop_editing()
        self.table.focus()
        self._refresh("Saved")

    def action_cancel(self) -> None:
        self.detail_panel.hide()
        self.table.focus()
        self.status.update("Cancelled")

    def action_quit(self) -> None:
        self.app.exit()


def replace_query(query: ListQuery, **changes: object) -> ListQuery:
    values = {
        "text": query.text,
        "filters": query.filters,
        "expression": query.expression,
        "sort": query.sort,
        "descending": query.descending,
        "limit": query.limit,
        "page": query.page,
        "page_size": query.page_size,
    }
    values.update(changes)
    return ListQuery(**values)


__all__ = ["ResourceBrowserScreen", "replace_query"]
