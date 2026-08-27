"""Owner-native Capital contract types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Capital")


if TYPE_CHECKING:
    from kairospy._native_capital_contract import (
        CancelFundingObjectiveRequest,
        CapitalAvailabilityResponse,
        CapitalClient,
        CapitalControlClient,
        CapitalControlRejectedError,
        CapitalControlResponse,
        CapitalControlUnavailableError,
        CapitalCurrentView,
        CapitalDemandResponse,
        CapitalHealth,
        CapitalInvalidCurrentViewError,
        CapitalInvalidEventError,
        CapitalInvalidInputError,
        CapitalCurrentViewUnavailableError,
        FundingLocation,
        ObserveCapitalDemandRequest,
        PublishFundingObjectiveRequest,
        QueryCapitalAvailabilityRequest,
        ReconcileCapitalPlanRequest,
        ReconcileCapitalPlanResponse,
        indexed_environment_path,
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
    ReconcileCapitalPlanResponse = _module.ReconcileCapitalPlanResponse
    FundingLocation = _module.FundingLocation
indexed_environment_path = _native().indexed_environment_path


__all__ = [
    "CancelFundingObjectiveRequest",
    "CapitalAvailabilityResponse",
    "CapitalClient",
    "CapitalControlClient",
    "CapitalControlRejectedError",
    "CapitalControlResponse",
    "CapitalControlUnavailableError",
    "CapitalCurrentView",
    "CapitalDemandResponse",
    "CapitalHealth",
    "CapitalInvalidCurrentViewError",
    "CapitalInvalidEventError",
    "CapitalInvalidInputError",
    "CapitalCurrentViewUnavailableError",
    "FundingLocation",
    "ObserveCapitalDemandRequest",
    "PublishFundingObjectiveRequest",
    "QueryCapitalAvailabilityRequest",
    "ReconcileCapitalPlanRequest",
    "ReconcileCapitalPlanResponse",
    "indexed_environment_path",
]
