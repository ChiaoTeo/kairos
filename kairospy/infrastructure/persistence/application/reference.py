from __future__ import annotations

from pathlib import Path

from kairospy.infrastructure.persistence.services.reference.sqlite_store import ReferenceStore, SqliteReferenceStore


def create_reference_store(root: str | Path = ".kairos/reference") -> SqliteReferenceStore:
    return SqliteReferenceStore(root)


__all__ = ["ReferenceStore", "SqliteReferenceStore", "create_reference_store"]
