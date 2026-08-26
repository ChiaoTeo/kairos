"""Python adapters for the Account v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AccountContractClient": (".control", "AccountContractClient"),
    "AccountCurrentViewReader": (".view", "AccountCurrentViewReader"),
    "AccountObservedOrdersViewReader": (".view", "AccountObservedOrdersViewReader"),
    "AccountIndexedViewReader": (".view", "AccountIndexedViewReader"),
    "account_indexed_environment_path": (".view", "account_indexed_environment_path"),
    "backtest_mark_to_market_request": (".runtime", "backtest_mark_to_market_request"),
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
