"""Public boundary for Workbench-owned navigation semantics.

Tree rendering is loaded lazily so the session can depend on route identities
without creating a session -> navigation package -> tree -> session cycle.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from .identity import NavigationContext, Routes, Section, belongs_to, route, starts_with

if TYPE_CHECKING:
    from .tree import (
        CommandContextView,
        action_id,
        back_target_items,
        back_targets,
        command_context,
        context_items,
        context_label,
        display_shortcut,
        go_back,
        menu_renderable,
        project_summary,
        record_description,
        record_label,
    )

_TREE_EXPORTS = {
    "CommandContextView",
    "action_id",
    "back_target_items",
    "back_targets",
    "command_context",
    "context_items",
    "context_label",
    "display_shortcut",
    "go_back",
    "menu_renderable",
    "project_summary",
    "record_description",
    "record_label",
}


def __getattr__(name: str):
    if name in _TREE_EXPORTS:
        return getattr(import_module(".tree", __name__), name)
    raise AttributeError(name)


__all__ = [
    "NavigationContext",
    "Routes",
    "Section",
    "CommandContextView",
    "action_id",
    "back_target_items",
    "back_targets",
    "command_context",
    "context_items",
    "context_label",
    "display_shortcut",
    "go_back",
    "menu_renderable",
    "project_summary",
    "record_description",
    "record_label",
    "belongs_to",
    "route",
    "starts_with",
]
