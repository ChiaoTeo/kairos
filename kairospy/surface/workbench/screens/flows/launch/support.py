"""Shared helpers for the Launch Workbench product slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def config_path(owner: Any, record: dict[str, Any]) -> Path:
    configured = record.get("config")
    return (
        Path(str(configured))
        if configured
        else owner.paths.launch_config(str(record["launch_id"]))
    )


def owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


__all__ = ["config_path", "owner"]
