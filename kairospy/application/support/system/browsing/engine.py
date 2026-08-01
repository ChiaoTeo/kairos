from __future__ import annotations

from math import ceil
from typing import Mapping, Sequence

import jmespath

from .models import ListQuery, ListResult


def query_rows(
    rows: Sequence[Mapping[str, object]],
    query: ListQuery,
    *,
    columns: Sequence[str] | None = None,
) -> ListResult:
    selected = list(rows)
    if query.text:
        needle = query.text.casefold()
        selected = [row for row in selected if needle in " ".join(_value(row, key) for key in row).casefold()]
    for key, expected in query.filters:
        selected = [row for row in selected if _matches(row.get(key), expected)]
    if query.expression:
        try:
            projected = jmespath.search(query.expression, selected)
        except jmespath.exceptions.JMESPathError as error:
            raise ValueError(f"invalid JMESPath query: {error}") from error
        if not isinstance(projected, list) or not all(isinstance(row, Mapping) for row in projected):
            raise ValueError("JMESPath query must return a list of objects")
        selected = list(projected)
    if query.sort:
        selected.sort(key=lambda row: _sort_value(row.get(query.sort)), reverse=query.descending)
    if query.limit is not None:
        selected = selected[:query.limit]
    total_rows = len(selected)
    start = (query.page - 1) * query.page_size
    page_rows = tuple(selected[start : start + query.page_size])
    visible_columns = tuple(columns or _columns(selected))
    return ListResult(page_rows, total_rows, query.page, query.page_size, max(1, ceil(total_rows / query.page_size)), visible_columns)


def parse_filters(values: Sequence[str] | None) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for value in values or ():
        key, separator, expected = value.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"filter must use key=value: {value}")
        result.append((key.strip(), expected.strip()))
    return tuple(result)


def _matches(value: object, expected: str) -> bool:
    if isinstance(value, bool):
        return value is (expected.casefold() in {"1", "true", "yes", "on"})
    return str(value).casefold() == expected.casefold()


def _sort_value(value: object) -> tuple[int, str]:
    return (value is None, "" if value is None else str(value).casefold())


def _value(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value)


def _columns(rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    seen: list[str] = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return tuple(seen)


__all__ = ["parse_filters", "query_rows"]
