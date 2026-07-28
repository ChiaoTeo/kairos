from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pickle
import sqlite3
from typing import Iterable

from kairospy.core.reference import ReferenceCatalog
from kairospy.core.reference.model import LifecycleEvent


@dataclass(frozen=True, slots=True)
class ReferenceStore:
    root: Path

    def __init__(self, root: str | Path = ".kairos/reference") -> None:
        object.__setattr__(self, "root", Path(root).expanduser())

    @property
    def database_path(self) -> Path:
        if self.root.suffix in {".db", ".sqlite", ".sqlite3"}:
            return self.root
        return self.root / "reference.sqlite"

    def save_catalog(self, catalog: ReferenceCatalog) -> None:
        with self._connect() as connection:
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
        with self._connect() as connection:
            _ensure_schema(connection)
            row = connection.execute("SELECT payload FROM reference_blobs WHERE name = 'catalog'").fetchone()
        if row is None:
            return ReferenceCatalog()
        value = pickle.loads(row[0])
        if not isinstance(value, ReferenceCatalog):
            raise TypeError(f"reference catalog store contains {type(value).__name__}, expected ReferenceCatalog")
        return value

    def append_events(self, events: Iterable[LifecycleEvent]) -> Path:
        existing = [*self.load_events(), *tuple(events)]
        with self._connect() as connection:
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
        with self._connect() as connection:
            _ensure_schema(connection)
            row = connection.execute("SELECT payload FROM reference_blobs WHERE name = 'lifecycle_events'").fetchone()
        if row is None:
            return ()
        value = pickle.loads(row[0])
        return tuple(value)

    def _connect(self) -> sqlite3.Connection:
        path = self.database_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reference_blobs (
            name TEXT PRIMARY KEY,
            payload BLOB NOT NULL
        )
        """
    )


__all__ = ["ReferenceStore"]
