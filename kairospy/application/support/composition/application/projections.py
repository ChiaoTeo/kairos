from __future__ import annotations

from pathlib import Path

from kairospy.application.support.query.projections.service import LaunchProjectionService
from kairospy.infrastructure.persistence.application.artifacts import (
    SqliteProjectionReader,
    find_projection_instance,
    list_projection_instances,
)


def launch_projection_query(instance_path: str | Path) -> LaunchProjectionService:
    """Compose the Biz projection query with the concrete persistence reader."""

    return LaunchProjectionService(SqliteProjectionReader(instance_path))


def find_latest_instance(root: str | Path, *, mode: str | None = None, launch_id: str | None = None) -> Path:
    return find_projection_instance(root, mode=mode, launch_id=launch_id)


def list_instances(root: str | Path, *, mode: str | None = None, launch_id: str | None = None) -> list[dict[str, object]]:
    return list_projection_instances(root, mode=mode, launch_id=launch_id)


__all__ = ["find_latest_instance", "launch_projection_query", "list_instances"]
