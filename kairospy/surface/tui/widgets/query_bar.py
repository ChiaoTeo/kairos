from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input

from kairospy.application.support.system.application.browsing import ListQuery, parse_filters


class QueryBar(Horizontal):
    """Search, filter, sort, and pagination inputs for list screens."""

    DEFAULT_CSS = """
    QueryBar {
        height: 3;
    }
    """

    def __init__(self, query: ListQuery, *, id: str = "query-bar") -> None:
        super().__init__(id=id)
        self.initial_query = query

    def compose(self) -> ComposeResult:
        yield Input(value=self.initial_query.text or "", placeholder="Search", id="search")
        yield Input(value=filters_text(self.initial_query), placeholder="Filter key=value", id="filter")
        yield Input(value=sort_text(self.initial_query), placeholder="Sort field or -field", id="sort")
        yield Input(value=str(self.initial_query.page), placeholder="Page", id="page")
        yield Input(value=str(self.initial_query.page_size), placeholder="Size", id="size")

    def read_query(self, current: ListQuery, *, reset_page: bool = True) -> ListQuery:
        search = self.query_one("#search", Input).value.strip()
        filter_text = self.query_one("#filter", Input).value.strip()
        sort_value = self.query_one("#sort", Input).value.strip()
        page_text = self.query_one("#page", Input).value.strip()
        size_text = self.query_one("#size", Input).value.strip()
        filters = parse_filters([item.strip() for item in filter_text.split(",") if item.strip()])
        descending = sort_value.startswith("-")
        page = max(1, int(page_text or "1"))
        page_size = max(1, int(size_text or "20"))
        return ListQuery(
            text=search or None,
            filters=filters,
            sort=sort_value.removeprefix("-") or None,
            descending=descending,
            limit=current.limit,
            page=1 if reset_page else page,
            page_size=page_size,
        )

    def set_page(self, page: int) -> None:
        self.query_one("#page", Input).value = str(page)


def filters_text(query: ListQuery) -> str:
    return ",".join(f"{key}={value}" for key, value in query.filters)


def sort_text(query: ListQuery) -> str:
    return f"-{query.sort}" if query.sort and query.descending else query.sort or ""


def query_summary(query: ListQuery) -> str:
    parts = []
    if query.text:
        parts.append(f"search: {query.text}")
    if query.filters:
        parts.append(f"filters: {filters_text(query)}")
    if query.sort:
        parts.append(f"sort: {sort_text(query)}")
    if query.expression:
        parts.append("query: JMESPath")
    return " | ".join(parts) if parts else "All rows"


__all__ = ["QueryBar", "filters_text", "query_summary", "sort_text"]
