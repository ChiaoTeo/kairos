"""Owner-native Risk contract types."""

from __future__ import annotations

from typing import Any

from kairospy.infrastructure.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Risk")


RiskClient = _native().RiskClient
RiskControlClient = _native().RiskControlClient
RiskCurrentView = _native().RiskCurrentView
RiskControlUnavailableError = _native().RiskControlUnavailableError
RiskControlRejectedError = _native().RiskControlRejectedError
RiskInvalidInputError = _native().RiskInvalidInputError
RiskInvalidEventError = _native().RiskInvalidEventError
RiskInvalidCurrentViewError = _native().RiskInvalidCurrentViewError
RiskCurrentViewUnavailableError = _native().RiskCurrentViewUnavailableError
RiskScope = _native().RiskScope
TradeRiskProposal = _native().TradeRiskProposal
RiskContext = _native().RiskContext
PublishPolicyRequest = _native().PublishPolicyRequest
AuthorizeRequest = _native().AuthorizeRequest
OpenCircuitRequest = _native().OpenCircuitRequest
CloseCircuitRequest = _native().CloseCircuitRequest
ReleaseReservationRequest = _native().ReleaseReservationRequest
ConsumeReservationRequest = _native().ConsumeReservationRequest
ResizeReservationRequest = _native().ResizeReservationRequest
AdvanceRiskTimeRequest = _native().AdvanceRiskTimeRequest


__all__ = [
    "AdvanceRiskTimeRequest", "AuthorizeRequest", "CloseCircuitRequest",
    "ConsumeReservationRequest", "OpenCircuitRequest", "PublishPolicyRequest",
    "ReleaseReservationRequest", "ResizeReservationRequest", "RiskClient",
    "RiskContext", "RiskControlClient", "RiskControlRejectedError",
    "RiskCurrentView",
    "RiskControlUnavailableError", "RiskScope", "TradeRiskProposal",
    "RiskInvalidInputError", "RiskInvalidEventError",
    "RiskInvalidCurrentViewError", "RiskCurrentViewUnavailableError",
]
