"""Python implementation of the Risk v2 cross-process contract."""

from importlib import import_module
from typing import Any


_EXPORTS = {
    "RiskControlClient": (".control", "RiskControlClient"),
    "RiskContractClient": (".control", "RiskControlClient"),
    "RiskLatestViewQueries": (".view", "RiskLatestViewQueries"),
    "RiskViewFrame": (".view", "RiskViewFrame"),
    "RiskViewKey": (".view", "RiskViewKey"),
    "RiskViewReader": (".view", "RiskViewReader"),
    "decode_event": (".events", "decode_event"),
    "decode_view": (".view", "decode_view"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    if name == "RiskContractClient":
        globals()[name] = value
    else:
        globals()[name] = value
    return value


__all__ = [
    "RiskControlClient",
    "RiskContractClient",
    "RiskLatestViewQueries",
    "RiskViewFrame",
    "RiskViewKey",
    "RiskViewReader",
    "decode_event",
    "decode_view",
]
