"""Python adapters for the Capital v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CapitalContractClient": (".client", "CapitalContractClient"),
    "CapitalCurrentViewQueries": (".view", "CapitalCurrentViewQueries"),
    "CapitalViewFrame": (".view", "CapitalViewFrame"),
    "CapitalViewKey": (".view", "CapitalViewKey"),
    "CapitalViewReader": (".view", "CapitalViewReader"),
    "decode_event": (".events", "decode_event"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
