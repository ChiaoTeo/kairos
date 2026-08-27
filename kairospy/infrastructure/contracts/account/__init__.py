"""Python adapters for the Account v2 cross-process contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AccountContractClient": (".types", "AccountClient"),
    "AccountClient": (".types", "AccountClient"),
    "AccountControlClient": (".types", "AccountControlClient"),
    "AccountSegmentsRequest": (".types", "AccountSegmentsRequest"),
    "AccountInvalidInputError": (".types", "AccountInvalidInputError"),
    "AccountInvalidEventError": (".types", "AccountInvalidEventError"),
    "AccountInvalidCurrentViewError": (".types", "AccountInvalidCurrentViewError"),
    "AccountCurrentViewUnavailableError": (".types", "AccountCurrentViewUnavailableError"),
    "AccountControlUnavailableError": (".types", "AccountControlUnavailableError"),
    "AccountControlRejectedError": (".types", "AccountControlRejectedError"),
    "MarkToMarketRequest": (".types", "MarkToMarketRequest"),
    "AdvanceAccountTimeRequest": (".types", "AdvanceAccountTimeRequest"),
    "SimulatedSettlement": (".types", "SimulatedSettlement"),
    "SimulatedCapitalMutation": (".types", "SimulatedCapitalMutation"),
    "SimulatedCapitalMutationQuery": (".types", "SimulatedCapitalMutationQuery"),
    "AccountCurrentView": (".types", "AccountCurrentView"),
    "indexed_environment_path": (".types", "indexed_environment_path"),
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
