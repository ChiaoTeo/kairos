"""Python implementation of the Risk v2 cross-process contract."""

from importlib import import_module
from typing import Any


_EXPORTS = {
    "RiskControlClient": (".control", "RiskControlClient"),
    "RiskContractClient": (".control", "RiskControlClient"),
    "RiskIndexedViewQueries": (".view", "RiskIndexedViewQueries"),
    "decode_event": (".events", "decode_event"),
    "risk_indexed_environment_path": (".view", "risk_indexed_environment_path"),
    "risk_indexed_key": (".view", "risk_indexed_key"),
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
    "RiskIndexedViewQueries",
    "decode_event",
    "risk_indexed_environment_path",
    "risk_indexed_key",
]
