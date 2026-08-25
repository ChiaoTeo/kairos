"""Typed presentation records used by numbered Workbench selections."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeVar


_RecordT = TypeVar("_RecordT")


@dataclass(frozen=True, slots=True)
class SelectionRecord:
    """One stable UI choice without exposing its payload shape to navigation."""

    key: str
    label: str
    description: str
    value: object


@dataclass(frozen=True, slots=True)
class ResourceRecordView(Mapping[str, object]):
    """Read-only resource payload after it crosses the UI boundary."""

    fields: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ResourceRecordView":
        return cls(dict(value))

    def __getitem__(self, key: str) -> object:
        return self.fields[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)


@dataclass(frozen=True, slots=True)
class LaunchRecordView(Mapping[str, object]):
    """Read-only Launch or instance payload retained by the presentation layer."""

    fields: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LaunchRecordView":
        return cls(dict(value))

    def merged(self, value: Mapping[str, object]) -> "LaunchRecordView":
        return type(self)({**self.fields, **value})

    def __getitem__(self, key: str) -> object:
        return self.fields[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)


def selection_records(
    records: Iterable[_RecordT],
    *,
    label: Callable[[_RecordT], str],
    description: Callable[[_RecordT], str],
    key: Callable[[_RecordT], str] | None = None,
) -> tuple[SelectionRecord, ...]:
    """Adapt owner results once when they cross into numbered UI state."""

    return tuple(
        record
        if isinstance(record, SelectionRecord)
        else SelectionRecord(
            key=(key or label)(record),
            label=label(record),
            description=description(record),
            value=record,
        )
        for record in records
    )


def selected_value(records: tuple[SelectionRecord, ...], value: str) -> object | None:
    """Resolve a transient numeric shortcut to its opaque owner value."""

    try:
        index = int(value) - 1
    except ValueError:
        return None
    return records[index].value if 0 <= index < len(records) else None


__all__ = [
    "LaunchRecordView",
    "ResourceRecordView",
    "SelectionRecord",
    "selected_value",
    "selection_records",
]
