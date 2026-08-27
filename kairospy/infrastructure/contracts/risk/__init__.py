"""Stable exports for the owner-native Risk contract."""

from importlib import import_module
from typing import Any


_EXPORTS = {
    "RiskClient": (".types", "RiskClient"),
    "RiskControlClient": (".types", "RiskControlClient"),
    "RiskInvalidInputError": (".types", "RiskInvalidInputError"),
    "RiskInvalidEventError": (".types", "RiskInvalidEventError"),
    "RiskInvalidCurrentViewError": (".types", "RiskInvalidCurrentViewError"),
    "RiskCurrentViewUnavailableError": (".types", "RiskCurrentViewUnavailableError"),
    "RiskControlUnavailableError": (".types", "RiskControlUnavailableError"),
    "RiskControlRejectedError": (".types", "RiskControlRejectedError"),
    "RiskContractClient": (".types", "RiskClient"),
    "AdvanceRiskTimeRequest": (".types", "AdvanceRiskTimeRequest"),
    "AuthorizeRequest": (".types", "AuthorizeRequest"),
    "CloseCircuitRequest": (".types", "CloseCircuitRequest"),
    "ConsumeReservationRequest": (".types", "ConsumeReservationRequest"),
    "OpenCircuitRequest": (".types", "OpenCircuitRequest"),
    "PublishPolicyRequest": (".types", "PublishPolicyRequest"),
    "ReleaseReservationRequest": (".types", "ReleaseReservationRequest"),
    "ResizeReservationRequest": (".types", "ResizeReservationRequest"),
    "RiskContext": (".types", "RiskContext"),
    "RiskScope": (".types", "RiskScope"),
    "TradeRiskProposal": (".types", "TradeRiskProposal"),
    "RiskCurrentView": (".types", "RiskCurrentView"),
    "decode_event": (".events", "decode_event"),
    "indexed_environment_path": (".types", "indexed_environment_path"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
