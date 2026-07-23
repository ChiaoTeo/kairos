from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from .ids import DatasetId, normalize_dataset_id
from .storage.store import DatasetStore


@dataclass(frozen=True, slots=True)
class DataSpaceId:
    """User-facing Data Space identity."""

    value: str

    def __post_init__(self) -> None:
        text = self.value.strip()
        if text != self.value or not _valid_segment(text):
            raise ValueError(f"invalid Data Space ID: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DataStreamId:
    """User-facing Data Stream identity, normally ``<space>.<stream>``."""

    value: str

    def __post_init__(self) -> None:
        text = self.value.strip()
        if text != self.value or not text:
            raise ValueError(f"invalid Data Stream ID: {self.value!r}")
        parts = text.split(".")
        if len(parts) < 2:
            raise ValueError(f"invalid Data Stream ID: {self.value!r}")
        for part in parts:
            if not _valid_segment(part):
                raise ValueError(f"invalid Data Stream ID segment {part!r} in {self.value!r}")

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.value.split("."))

    @property
    def space(self) -> DataSpaceId:
        return DataSpaceId(self.parts[0])

    @property
    def stream(self) -> str:
        return ".".join(self.parts[1:])

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DataStreamRef:
    """Resolved bridge from Space/Stream UX language to current Dataset storage."""

    stream_id: DataStreamId
    dataset_id: DatasetId
    source: str

    @property
    def space(self) -> DataSpaceId:
        return self.stream_id.space

    @property
    def stream(self) -> str:
        return self.stream_id.stream

    def to_payload(self) -> dict[str, str]:
        return {
            "stream": str(self.stream_id),
            "space": str(self.space),
            "name": self.stream,
            "dataset": str(self.dataset_id),
            "source": self.source,
        }


class DataStreamResolver:
    """Resolve new Stream IDs through the existing DatasetStore compatibility layer."""

    def __init__(self, store: DatasetStore | str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.store = store if isinstance(store, DatasetStore) else DatasetStore(store)

    def resolve(self, stream_or_dataset: object) -> DataStreamRef:
        stream_id = normalize_stream_id(stream_or_dataset)
        dataset_id = self.store.resolve(stream_id)
        source = "alias" if str(dataset_id) != str(stream_id) else "stream"
        return DataStreamRef(stream_id=stream_id, dataset_id=dataset_id, source=source)

    def match(self, pattern: object) -> tuple[DataStreamRef, ...]:
        text = str(pattern)
        refs = []
        seen: set[str] = set()
        for dataset in self.store.list_datasets():
            value = str(dataset)
            if fnmatchcase(value, text):
                refs.append(DataStreamRef(normalize_stream_id(value), dataset, "dataset"))
                seen.add(value)
        for alias, dataset in self.store.aliases().items():
            if fnmatchcase(alias, text) and dataset not in seen:
                refs.append(DataStreamRef(normalize_stream_id(alias), normalize_dataset_id(dataset), "alias"))
                seen.add(dataset)
        return tuple(sorted(refs, key=lambda item: str(item.stream_id)))


def normalize_stream_id(value: object) -> DataStreamId:
    if isinstance(value, DataStreamId):
        return value
    return DataStreamId(str(value))


def _valid_segment(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return all(character.isalnum() or character in {"_", "-"} for character in value)
