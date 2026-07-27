from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class OutputFormat(StrEnum):
    ROWS = "rows"


@dataclass(frozen=True, slots=True)
class DataQuery:
    start: object | None = None
    end: object | None = None
    columns: tuple[str, ...] = ()
    limit: int | None = None
    output: OutputFormat = OutputFormat.ROWS

    @classmethod
    def from_values(
        cls,
        *,
        start: object | None = None,
        end: object | None = None,
        columns: Iterable[str] | None = None,
        limit: int | None = None,
        output: str | OutputFormat = OutputFormat.ROWS,
    ) -> "DataQuery":
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        parsed_output = output if isinstance(output, OutputFormat) else OutputFormat(str(output))
        return cls(
            start=start,
            end=end,
            columns=tuple(str(item) for item in columns or ()),
            limit=limit,
            output=parsed_output,
        )

