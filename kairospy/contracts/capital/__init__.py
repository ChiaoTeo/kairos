"""Python facade for the Capital v2 owner contract."""

from importlib import import_module
from typing import Any

from .events import __all__ as _EVENT_EXPORTS


_EXPORTS = {
    "CapitalContractClient": (".types", "CapitalClient"),
    "CapitalClient": (".types", "CapitalClient"),
    "CapitalCurrentView": (".types", "CapitalCurrentView"),
    "decode_event": (".events", "decode_event"),
    "CapitalControlClient": (".types", "CapitalControlClient"),
    "CapitalInvalidInputError": (".types", "CapitalInvalidInputError"),
    "CapitalInvalidEventError": (".types", "CapitalInvalidEventError"),
    "CapitalInvalidCurrentViewError": (".types", "CapitalInvalidCurrentViewError"),
    "CapitalCurrentViewUnavailableError": (
        ".types",
        "CapitalCurrentViewUnavailableError",
    ),
    "CapitalControlUnavailableError": (".types", "CapitalControlUnavailableError"),
    "CapitalControlRejectedError": (".types", "CapitalControlRejectedError"),
    "CancelFundingObjectiveRequest": (".types", "CancelFundingObjectiveRequest"),
    "FundingLocation": (".types", "FundingLocation"),
    "ObserveCapitalDemandRequest": (".types", "ObserveCapitalDemandRequest"),
    "PublishFundingObjectiveRequest": (".types", "PublishFundingObjectiveRequest"),
    "QueryCapitalAvailabilityRequest": (".types", "QueryCapitalAvailabilityRequest"),
    "ReconcileCapitalPlanRequest": (".types", "ReconcileCapitalPlanRequest"),
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
