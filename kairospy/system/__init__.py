"""Workspace, configuration, launch, and process lifecycle subsystem."""

from __future__ import annotations

from typing import Any

__all__ = ["SystemApplication", "open_system"]


def open_system(workspace: Any):
    """Open the aggregate System application for one resolved workspace."""

    from .composition import compose_system_application

    return compose_system_application(workspace)


def __getattr__(name: str) -> Any:
    if name == "SystemApplication":
        from .application import SystemApplication

        return SystemApplication
    raise AttributeError(name)
