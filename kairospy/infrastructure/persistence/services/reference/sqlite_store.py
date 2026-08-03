from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import io
import pickle
import sqlite3
from typing import Iterable

from kairospy.domain.reference import ReferenceCatalog
from kairospy.domain.reference.model import LifecycleEvent
from kairospy.infrastructure.persistence.services.storage.sqlite import sqlite_connection


@dataclass(frozen=True, slots=True)
class SqliteReferenceStore:
    root: Path

    def __init__(self, root: str | Path = ".kairos/reference") -> None:
        object.__setattr__(self, "root", Path(root).expanduser())

    @property
    def database_path(self) -> Path:
        if self.root.suffix in {".db", ".sqlite", ".sqlite3"}:
            return self.root
        return self.root / "reference.sqlite"

    def save_catalog(self, catalog: ReferenceCatalog) -> None:
        with sqlite_connection(self.database_path) as connection:
            _ensure_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO reference_blobs (name, payload)
                    VALUES ('catalog', ?)
                    ON CONFLICT(name) DO UPDATE SET payload = excluded.payload
                    """,
                    (pickle.dumps(catalog),),
                )

    def load_catalog(self) -> ReferenceCatalog:
        if not self.database_path.exists():
            return ReferenceCatalog()
        with sqlite_connection(self.database_path) as connection:
            _ensure_schema(connection)
            row = connection.execute("SELECT payload FROM reference_blobs WHERE name = 'catalog'").fetchone()
        if row is None:
            return ReferenceCatalog()
        value = _loads_reference_payload(row[0])
        if not isinstance(value, ReferenceCatalog):
            raise TypeError(f"reference catalog store contains {type(value).__name__}, expected ReferenceCatalog")
        return value

    def append_events(self, events: Iterable[LifecycleEvent]) -> Path:
        existing = [*self.load_events(), *tuple(events)]
        with sqlite_connection(self.database_path) as connection:
            _ensure_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO reference_blobs (name, payload)
                    VALUES ('lifecycle_events', ?)
                    ON CONFLICT(name) DO UPDATE SET payload = excluded.payload
                    """,
                    (pickle.dumps(tuple(existing)),),
                )
        return self.database_path

    def load_events(self) -> tuple[LifecycleEvent, ...]:
        if not self.database_path.exists():
            return ()
        with sqlite_connection(self.database_path) as connection:
            _ensure_schema(connection)
            row = connection.execute("SELECT payload FROM reference_blobs WHERE name = 'lifecycle_events'").fetchone()
        if row is None:
            return ()
        value = _loads_reference_payload(row[0])
        return tuple(value)

def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reference_blobs (
            name TEXT PRIMARY KEY,
            payload BLOB NOT NULL
        )
        """
    )


class _ReferencePayloadUnpickler(pickle.Unpickler):
    """Load reference records written before the domain module move.

    The reference model was moved from ``kairospy.core.reference`` to
    ``kairospy.domain.reference`` without changing its persisted object
    shape.  Pickle stores the defining module, so old records otherwise fail
    before the store can validate them.  Keep the compatibility scope narrow:
    only the former reference package is remapped.
    """

    _LEGACY_PREFIX = "kairospy.core.reference"
    _CURRENT_PREFIX = "kairospy.domain.reference"

    def find_class(self, module: str, name: str):
        if module == self._LEGACY_PREFIX or module.startswith(self._LEGACY_PREFIX + "."):
            module = self._CURRENT_PREFIX + module[len(self._LEGACY_PREFIX) :]
        return super().find_class(module, name)


def _loads_reference_payload(payload: bytes) -> object:
    return _ReferencePayloadUnpickler(io.BytesIO(payload)).load()


ReferenceStore = SqliteReferenceStore


__all__ = ["ReferenceStore", "SqliteReferenceStore"]
