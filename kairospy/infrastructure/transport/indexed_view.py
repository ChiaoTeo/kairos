"""Owned Python reads over Kairos LMDB indexed current views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IndexedViewSchema:
    database: str
    key_version: int
    value_schema: str
    value_version: int

    def native_tuple(self) -> tuple[str, int, str, int]:
        return (
            self.database,
            self.key_version,
            self.value_schema,
            self.value_version,
        )


@dataclass(frozen=True, slots=True)
class IndexedViewMetadata:
    format_version: int
    resource_epoch: int
    producer_incarnation: int
    applied_event_sequence: int
    committed_at_unix_nanos: int
    rebuild_state: str
    diagnostic_code: str | None


@dataclass(frozen=True, slots=True)
class IndexedViewSnapshot:
    metadata: IndexedViewMetadata
    rows: dict[str, tuple[tuple[bytes, bytes], ...]]


class _NativeMetadata(Protocol):
    format_version: int
    resource_epoch: int
    producer_incarnation: int
    applied_event_sequence: int
    committed_at_unix_nanos: int
    rebuild_state: str
    diagnostic_code: str | None


class _NativeReader(Protocol):
    def metadata(self) -> _NativeMetadata: ...

    def get(self, database: str, key: bytes) -> bytes | None: ...

    def value_snapshot(
        self, database: str, key: bytes
    ) -> tuple[_NativeMetadata, bytes | None]: ...

    def prefix(
        self, database: str, prefix: bytes, limit: int
    ) -> list[tuple[bytes, bytes]]: ...

    def snapshot(
        self, requests: list[tuple[str, bytes, int]]
    ) -> tuple[_NativeMetadata, dict[str, list[tuple[bytes, bytes]]]]: ...

    def close(self) -> None: ...


class IndexedViewReader:
    """Short-transaction reader returning only owned keys and values."""

    def __init__(
        self,
        path: str | Path,
        *,
        map_size: int,
        workspace_id: str,
        launch_id: str | None,
        instance_id: str | None,
        owner: str,
        publisher_resource_id: str,
        resource_epoch: int,
        schemas: tuple[IndexedViewSchema, ...],
    ) -> None:
        self.path = Path(path)
        self._configuration = (
            map_size,
            workspace_id,
            launch_id,
            instance_id,
            owner,
            publisher_resource_id,
            resource_epoch,
            [schema.native_tuple() for schema in schemas],
        )
        self._reader: _NativeReader | None = None

    def _open(self) -> _NativeReader:
        reader = self._reader
        if reader is None:
            from .native import native

            reader = native.IndexedViewReader(self.path, *self._configuration)
            self._reader = reader
        return reader

    def metadata(self) -> IndexedViewMetadata:
        return _metadata(self._open().metadata())

    def get(self, database: str, key: bytes) -> bytes | None:
        value = self._open().get(database, key)
        return None if value is None else bytes(value)

    def value_snapshot(
        self, database: str, key: bytes
    ) -> tuple[IndexedViewMetadata, bytes | None]:
        metadata, value = self._open().value_snapshot(database, key)
        return _metadata(metadata), None if value is None else bytes(value)

    def prefix(
        self, database: str, prefix: bytes, *, limit: int
    ) -> tuple[tuple[bytes, bytes], ...]:
        return tuple(
            (bytes(key), bytes(value))
            for key, value in self._open().prefix(database, prefix, limit)
        )

    def snapshot(
        self, requests: tuple[tuple[str, bytes, int], ...]
    ) -> IndexedViewSnapshot:
        metadata, rows = self._open().snapshot(list(requests))
        return IndexedViewSnapshot(
            metadata=_metadata(metadata),
            rows={
                database: tuple((bytes(key), bytes(value)) for key, value in values)
                for database, values in rows.items()
            },
        )

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def __enter__(self) -> IndexedViewReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _metadata(value: _NativeMetadata) -> IndexedViewMetadata:
    return IndexedViewMetadata(
        format_version=int(value.format_version),
        resource_epoch=int(value.resource_epoch),
        producer_incarnation=int(value.producer_incarnation),
        applied_event_sequence=int(value.applied_event_sequence),
        committed_at_unix_nanos=int(value.committed_at_unix_nanos),
        rebuild_state=str(value.rebuild_state),
        diagnostic_code=(
            None if value.diagnostic_code is None else str(value.diagnostic_code)
        ),
    )


__all__ = [
    "IndexedViewMetadata",
    "IndexedViewReader",
    "IndexedViewSchema",
    "IndexedViewSnapshot",
]
