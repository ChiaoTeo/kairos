"""Python adapters for the Execution v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ExecutionControlClient": (".control", "ExecutionControlClient"),
    "ExecutionCurrentViews": (".current", "ExecutionCurrentViews"),
    "ExecutionIndexedViewReader": (".view", "ExecutionIndexedViewReader"),
    "decode_event": (".events", "decode_event"),
    "execution_indexed_environment_path": (
        ".view",
        "execution_indexed_environment_path",
    ),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
