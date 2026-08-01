from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from textual.widgets import DataTable


class ResourceTable(DataTable[str]):
    """Table widget used by Kairos resource screens."""

    def __init__(self, *, id: str = "table") -> None:
        super().__init__(id=id, cursor_type="row", zebra_stripes=True, show_row_labels=False)

    def replace_rows(self, columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
        self.clear(columns=True)
        self.add_columns(*columns)
        self.add_rows(tuple(cell_text(row.get(column)) for column in columns) for row in rows)


def cell_text(value: object) -> str:
    return "-" if value is None else str(value)


__all__ = ["ResourceTable", "cell_text"]
