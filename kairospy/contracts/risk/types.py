"""Canonical Risk owner-contract types exported by the native binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Risk")


if TYPE_CHECKING:
    from kairospy._native_risk_contract import (
        AdvanceRiskTimeRequest,
        AuthorizeRequest,
        CloseCircuitRequest,
        ConsumeReservationRequest,
        OpenCircuitRequest,
        PublishPolicyRequest,
        ReleaseReservationRequest,
        ResizeReservationRequest,
        RiskCircuit,
        RiskClient,
        RiskContext,
        RiskControlClient,
        RiskControlRejectedError,
        RiskControlUnavailableError,
        RiskCurrentSnapshot,
        RiskCurrentView,
        RiskCurrentViewUnavailableError,
        RiskInvalidCurrentViewError,
        RiskInvalidEventError,
        RiskInvalidInputError,
        RiskLimitUsage,
        RiskPolicy,
        RiskScope,
        TradeRiskProposal,
    )
else:
    _module = _native()
    AdvanceRiskTimeRequest = _module.AdvanceRiskTimeRequest
    AuthorizeRequest = _module.AuthorizeRequest
    CloseCircuitRequest = _module.CloseCircuitRequest
    ConsumeReservationRequest = _module.ConsumeReservationRequest
    OpenCircuitRequest = _module.OpenCircuitRequest
    PublishPolicyRequest = _module.PublishPolicyRequest
    ReleaseReservationRequest = _module.ReleaseReservationRequest
    ResizeReservationRequest = _module.ResizeReservationRequest
    RiskCircuit = _module.RiskCircuit
    RiskClient = _module.RiskClient
    RiskContext = _module.RiskContext
    RiskControlClient = _module.RiskControlClient
    RiskControlRejectedError = _module.RiskControlRejectedError
    RiskControlUnavailableError = _module.RiskControlUnavailableError
    RiskCurrentSnapshot = _module.RiskCurrentSnapshot
    RiskCurrentView = _module.RiskCurrentView
    RiskCurrentViewUnavailableError = _module.RiskCurrentViewUnavailableError
    RiskInvalidCurrentViewError = _module.RiskInvalidCurrentViewError
    RiskInvalidEventError = _module.RiskInvalidEventError
    RiskInvalidInputError = _module.RiskInvalidInputError
    RiskLimitUsage = _module.RiskLimitUsage
    RiskPolicy = _module.RiskPolicy
    RiskScope = _module.RiskScope
    TradeRiskProposal = _module.TradeRiskProposal


__all__ = [
    "AdvanceRiskTimeRequest",
    "AuthorizeRequest",
    "CloseCircuitRequest",
    "ConsumeReservationRequest",
    "OpenCircuitRequest",
    "PublishPolicyRequest",
    "ReleaseReservationRequest",
    "ResizeReservationRequest",
    "RiskCircuit",
    "RiskClient",
    "RiskContext",
    "RiskControlClient",
    "RiskControlRejectedError",
    "RiskControlUnavailableError",
    "RiskCurrentSnapshot",
    "RiskCurrentView",
    "RiskCurrentViewUnavailableError",
    "RiskInvalidCurrentViewError",
    "RiskInvalidEventError",
    "RiskInvalidInputError",
    "RiskLimitUsage",
    "RiskPolicy",
    "RiskScope",
    "TradeRiskProposal",
]
