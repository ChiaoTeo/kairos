from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Sequence, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if self.page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")


@dataclass(frozen=True, slots=True)
class PageResult:
    page: int
    page_size: int
    total_rows: int
    total_pages: int

    def to_dict(self) -> dict[str, int]:
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total_rows": self.total_rows,
            "total_pages": self.total_pages,
        }


def paginate(rows: Sequence[T], request: PageRequest) -> tuple[tuple[T, ...], PageResult]:
    total_rows = len(rows)
    total_pages = max(1, ceil(total_rows / request.page_size))
    start = (request.page - 1) * request.page_size
    end = start + request.page_size
    return tuple(rows[start:end]), PageResult(request.page, request.page_size, total_rows, total_pages)


__all__ = ["PageRequest", "PageResult", "paginate"]
