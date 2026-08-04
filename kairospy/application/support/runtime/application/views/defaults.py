from __future__ import annotations

from .registry import ViewRegistry


def default_view_registry() -> ViewRegistry:
    return ViewRegistry()


__all__ = ["default_view_registry"]
