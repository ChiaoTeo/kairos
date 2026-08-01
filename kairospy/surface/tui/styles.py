from __future__ import annotations


RESOURCE_BROWSER_CSS = """
Screen {
    layout: vertical;
    background: $background;
    color: $text;
}

Header {
    background: $surface;
    color: $text;
}

#command-bar {
    height: 5;
    padding: 0 1;
    background: $surface;
    border-bottom: solid $primary;
}

#summary {
    height: 1;
    color: $text-muted;
    padding: 0 1;
}

#query-bar {
    height: 3;
}

#query-bar Input {
    width: 1fr;
    height: 3;
    margin-right: 1;
    border: tall $surface-lighten-2;
    background: $boost;
}

#query-bar Input:focus {
    border: tall $primary;
}

#query-bar #page {
    width: 12;
}

#query-bar #size {
    width: 12;
    margin-right: 0;
}

#body {
    height: 1fr;
    padding: 1;
    background: $background;
}

#table-panel {
    height: 1fr;
    border: round $surface-lighten-2;
    background: $panel;
}

#detail-panel {
    height: 12;
    margin-top: 1;
    border: round $surface-lighten-2;
    background: $panel;
    display: none;
}

.panel-title {
    height: 1;
    padding: 0 1;
    background: $surface;
    color: $primary;
    text-style: bold;
}

#table {
    height: 1fr;
    background: $panel;
}

DataTable {
    scrollbar-background: $panel;
    scrollbar-color: $primary;
}

DataTable > .datatable--header {
    background: $surface;
    color: $text;
    text-style: bold;
}

DataTable > .datatable--cursor {
    background: $primary;
    color: $text;
    text-style: bold;
}

#detail {
    height: 1fr;
    padding: 1;
    overflow: auto;
    background: $panel;
}

#editor {
    height: 10;
    border: tall $warning;
    display: none;
    background: $boost;
}

#status {
    height: 1;
    padding: 0 1;
    background: $surface;
    color: $text-muted;
}

Footer {
    background: $surface;
}
"""


__all__ = ["RESOURCE_BROWSER_CSS"]
