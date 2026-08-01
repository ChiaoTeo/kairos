from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ListQuery:
    """Transport-neutral query shared by list and interactive surfaces."""

    text: str | None = None
    filters: tuple[tuple[str, str], ...] = ()
    expression: str | None = None
    sort: str | None = None
    descending: bool = False
    limit: int | None = None
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        if self.page < 1 or self.page_size < 1 or (self.limit is not None and self.limit < 1):
            raise ValueError("page and page_size must be greater than or equal to 1; limit must be positive")


@dataclass(frozen=True, slots=True)
class ListResult:
    rows: tuple[Mapping[str, object], ...]
    total_rows: int
    page: int
    page_size: int
    total_pages: int
    columns: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "rows": [dict(row) for row in self.rows],
            "page": {
                "page": self.page,
                "page_size": self.page_size,
                "total_rows": self.total_rows,
                "total_pages": self.total_pages,
            },
            "columns": list(self.columns),
        }


__all__ = ["ListQuery", "ListResult"]
