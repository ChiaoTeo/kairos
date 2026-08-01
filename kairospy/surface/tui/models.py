from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from kairospy.application.browsing import ListQuery


DetailReader = Callable[[Mapping[str, object]], object]
SaveEditor = Callable[[Mapping[str, object]], object]


@dataclass
class ResourceList:
    """Data and callbacks consumed by resource list screens."""

    rows: tuple[Mapping[str, object], ...]
    columns: tuple[str, ...] = ()
    title: str = "Browse"
    detail: DetailReader | None = None
    save: SaveEditor | None = None
    query: ListQuery | None = None

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
        *,
        columns: Sequence[str] = (),
        title: str = "Browse",
        detail: DetailReader | None = None,
        save: SaveEditor | None = None,
        page_size: int = 20,
        query: ListQuery | None = None,
    ) -> ResourceList:
        return cls(
            rows=tuple(rows),
            columns=tuple(columns),
            title=title,
            detail=detail,
            save=save,
            query=query or ListQuery(page_size=page_size),
        )


__all__ = ["DetailReader", "ResourceList", "SaveEditor"]
