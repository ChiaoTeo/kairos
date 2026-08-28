"""Python facade for the Execution v2 owner contract."""

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
}
from .events import __all__ as _EVENT_EXPORTS
_EXPORTS.update({name: (".events", name) for name in _EVENT_EXPORTS})


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
