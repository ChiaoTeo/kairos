"""Python adapters for the Market v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "MarketControlClient": (".control", "MarketControlClient"),
    "MarketIndexedFrame": (".view", "MarketIndexedFrame"),
    "MarketIndexedViewQueries": (".view", "MarketIndexedViewQueries"),
    "MarketQuoteCurrent": (".view", "MarketQuoteCurrent"),
    "MarketViewKey": (".view", "MarketViewKey"),
    "MarketViewKind": (".view", "MarketViewKind"),
    "market_indexed_environment_path": (".view", "market_indexed_environment_path"),
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
