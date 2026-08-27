"""Python adapters for the Execution v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ExecutionClient": (".types", "ExecutionClient"),
    "ExecutionControlClient": (".types", "ExecutionControlClient"),
    "ExecutionControlRejectedError": (".types", "ExecutionControlRejectedError"),
    "ExecutionControlUnavailableError": (".types", "ExecutionControlUnavailableError"),
    "ExecutionInvalidInputError": (".types", "ExecutionInvalidInputError"),
    "ExecutionInvalidCurrentViewError": (".types", "ExecutionInvalidCurrentViewError"),
    "ExecutionCurrentViewUnavailableError": (".types", "ExecutionCurrentViewUnavailableError"),
    "ExecutionCurrentView": (".types", "ExecutionCurrentView"),
    "decode_event": (".events", "decode_event"),
    "ExecutionEvent": (".events", "ExecutionEvent"),
    "ExecutionInvalidEventError": (".events", "ExecutionInvalidEventError"),
    "indexed_environment_path": (".types", "indexed_environment_path"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
