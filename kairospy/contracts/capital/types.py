"""Owner-native Capital contract types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Capital")


if TYPE_CHECKING:
    from kairospy._native_capital_contract import (
        CancelFundingObjectiveRequest,
        CapitalAvailabilityResponse,
        CapitalAvailability,
        CapitalAlert,
        CapitalClient,
        CapitalControlClient,
        CapitalControlRejectedError,
        CapitalControlResponse,
        CapitalControlUnavailableError,
        CapitalCurrentView,
        CapitalCurrentSnapshot,
        CapitalDemandResponse,
        CapitalHealth,
        CapitalInvalidCurrentViewError,
        CapitalInvalidEventError,
        CapitalInvalidInputError,
        CapitalCurrentViewUnavailableError,
        FundingLocation,
        FundingHorizon,
        ObserveCapitalDemandRequest,
        PublishFundingObjectiveRequest,
        QueryCapitalAvailabilityRequest,
        ReconcileCapitalPlanRequest,
        ReconcileCapitalPlanResponse,
    )
else:
    _module = _native()
    CapitalClient = _module.CapitalClient
    CapitalControlClient = _module.CapitalControlClient
    CapitalCurrentView = _module.CapitalCurrentView
    CapitalControlUnavailableError = _module.CapitalControlUnavailableError
    CapitalControlRejectedError = _module.CapitalControlRejectedError
    PublishFundingObjectiveRequest = _module.PublishFundingObjectiveRequest
    CancelFundingObjectiveRequest = _module.CancelFundingObjectiveRequest
    ObserveCapitalDemandRequest = _module.ObserveCapitalDemandRequest
    QueryCapitalAvailabilityRequest = _module.QueryCapitalAvailabilityRequest
    ReconcileCapitalPlanRequest = _module.ReconcileCapitalPlanRequest
    CapitalHealth = _module.CapitalHealth
    CapitalInvalidCurrentViewError = _module.CapitalInvalidCurrentViewError
    CapitalInvalidEventError = _module.CapitalInvalidEventError
    CapitalInvalidInputError = _module.CapitalInvalidInputError
    CapitalCurrentViewUnavailableError = _module.CapitalCurrentViewUnavailableError
    CapitalControlResponse = _module.CapitalControlResponse
    CapitalDemandResponse = _module.CapitalDemandResponse
    CapitalAvailabilityResponse = _module.CapitalAvailabilityResponse
    CapitalAvailability = _module.CapitalAvailability
    CapitalAlert = _module.CapitalAlert
    CapitalCurrentSnapshot = _module.CapitalCurrentSnapshot
    FundingHorizon = _module.FundingHorizon
    ReconcileCapitalPlanResponse = _module.ReconcileCapitalPlanResponse
    FundingLocation = _module.FundingLocation


__all__ = [
    "CancelFundingObjectiveRequest",
    "CapitalAvailabilityResponse",
    "CapitalAvailability",
    "CapitalAlert",
    "CapitalClient",
    "CapitalControlClient",
    "CapitalControlRejectedError",
    "CapitalControlResponse",
    "CapitalControlUnavailableError",
    "CapitalCurrentView",
    "CapitalCurrentSnapshot",
    "CapitalDemandResponse",
    "CapitalHealth",
    "CapitalInvalidCurrentViewError",
    "CapitalInvalidEventError",
    "CapitalInvalidInputError",
    "CapitalCurrentViewUnavailableError",
    "FundingLocation",
    "FundingHorizon",
    "ObserveCapitalDemandRequest",
    "PublishFundingObjectiveRequest",
    "QueryCapitalAvailabilityRequest",
    "ReconcileCapitalPlanRequest",
    "ReconcileCapitalPlanResponse",
]
