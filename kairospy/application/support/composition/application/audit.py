"""Composition of persisted execution audit query implementations."""

from __future__ import annotations

from pathlib import Path

from kairospy.application.usecases.execution.application.audit import OrderAuditQueries
from kairospy.infrastructure.persistence.services.execution.sqlite_audit import (
    SqliteOrderAuditDirectory,
    SqliteOrderAuditStore,
)


def build_order_audit_queries(*, db: Path | None, root: Path, instance_id: str | None) -> OrderAuditQueries:
    store = SqliteOrderAuditStore(db, instance_id=instance_id or "cli") if db is not None else SqliteOrderAuditDirectory(root)
    return OrderAuditQueries(store)


__all__ = ["build_order_audit_queries"]
