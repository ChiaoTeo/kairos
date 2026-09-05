"""Python facade for the Account v2 owner contract."""

from importlib import import_module
from typing import Any

from .events import __all__ as _EVENT_EXPORTS


_EXPORTS = {
    "AccountContractClient": (".types", "AccountClient"),
    "AccountClient": (".types", "AccountClient"),
    "AccountBalanceCurrent": (".types", "AccountBalanceCurrent"),
    "AccountCollateralCurrent": (".types", "AccountCollateralCurrent"),
    "AccountControlClient": (".types", "AccountControlClient"),
    "AccountSegmentsRequest": (".types", "AccountSegmentsRequest"),
    "AccountInvalidInputError": (".types", "AccountInvalidInputError"),
    "AccountInvalidEventError": (".types", "AccountInvalidEventError"),
    "AccountInvalidCurrentViewError": (".types", "AccountInvalidCurrentViewError"),
    "AccountCurrentViewUnavailableError": (
        ".types",
        "AccountCurrentViewUnavailableError",
    ),
    "AccountControlUnavailableError": (".types", "AccountControlUnavailableError"),
    "AccountControlRejectedError": (".types", "AccountControlRejectedError"),
    "MarkToMarketRequest": (".types", "MarkToMarketRequest"),
    "AdvanceAccountTimeRequest": (".types", "AdvanceAccountTimeRequest"),
    "SimulatedSettlement": (".types", "SimulatedSettlement"),
    "SimulatedCapitalMutation": (".types", "SimulatedCapitalMutation"),
    "SimulatedCapitalMutationQuery": (".types", "SimulatedCapitalMutationQuery"),
    "AccountCurrentView": (".types", "AccountCurrentView"),
    "AccountCurrentSnapshot": (".types", "AccountCurrentSnapshot"),
    "AccountEarnHoldingCurrent": (".types", "AccountEarnHoldingCurrent"),
    "AccountObservedOrderCurrent": (".types", "AccountObservedOrderCurrent"),
    "AccountPositionCurrent": (".types", "AccountPositionCurrent"),
    "AccountSegmentCurrent": (".types", "AccountSegmentCurrent"),
    "decode_event": (".events", "decode_event"),
}
_EXPORTS.update({name: (".events", name) for name in _EVENT_EXPORTS})


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
