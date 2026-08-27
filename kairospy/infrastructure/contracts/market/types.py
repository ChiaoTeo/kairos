"""Canonical Market owner-contract types exported by the native binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract

if TYPE_CHECKING:
    from kairospy._native_market_contract import (
        ExpiryRange,
        MarketCommandStatus,
        MarketClient,
        MarketControlClient,
        MarketControlRejectedError,
        MarketControlUnavailableError,
        MarketCurrentView,
        MarketDataRoute,
        MarketDataRoutesResponse,
        MarketHealthResponse,
        MarketInvalidCurrentViewError,
        MarketInvalidEventError,
        MarketInvalidInputError,
        MarketCurrentViewUnavailableError,
        MarketReleaseOwnerResponse,
        MarketSubscriptionRequest,
        MarketSubscriptionResponse,
        MarketTarget,
        ObservationRequirement,
        OptionFilter,
        OptionRight,
        Options,
        Provider,
        ProviderPreference,
        StrikeRange,
    )


def _native() -> Any:
    return load_owner_contract("Market")


if not TYPE_CHECKING:
    Provider = _native().Provider
    ObservationRequirement = _native().ObservationRequirement
    ProviderPreference = _native().ProviderPreference
    OptionRight = _native().OptionRight
    ExpiryRange = _native().ExpiryRange
    StrikeRange = _native().StrikeRange
    OptionFilter = _native().OptionFilter
    Options = _native().Options
    MarketTarget = _native().MarketTarget
    MarketSubscriptionRequest = _native().MarketSubscriptionRequest
    MarketClient = _native().MarketClient
    MarketControlClient = _native().MarketControlClient
    MarketCurrentView = _native().MarketCurrentView
    MarketHealthResponse = _native().MarketHealthResponse
    MarketInvalidCurrentViewError = _native().MarketInvalidCurrentViewError
    MarketInvalidEventError = _native().MarketInvalidEventError
    MarketInvalidInputError = _native().MarketInvalidInputError
    MarketCurrentViewUnavailableError = _native().MarketCurrentViewUnavailableError
    MarketDataRoute = _native().MarketDataRoute
    MarketDataRoutesResponse = _native().MarketDataRoutesResponse
    MarketSubscriptionResponse = _native().MarketSubscriptionResponse
    MarketCommandStatus = _native().MarketCommandStatus
    MarketReleaseOwnerResponse = _native().MarketReleaseOwnerResponse
    MarketControlUnavailableError = _native().MarketControlUnavailableError
    MarketControlRejectedError = _native().MarketControlRejectedError


__all__ = [
    "MarketSubscriptionRequest",
    "MarketSubscriptionResponse",
    "MarketTarget",
    "MarketCommandStatus",
    "MarketClient",
    "MarketControlClient",
    "MarketControlRejectedError",
    "MarketControlUnavailableError",
    "MarketCurrentView",
    "MarketDataRoute",
    "MarketDataRoutesResponse",
    "MarketHealthResponse",
    "MarketInvalidCurrentViewError",
    "MarketInvalidEventError",
    "MarketInvalidInputError",
    "MarketCurrentViewUnavailableError",
    "MarketReleaseOwnerResponse",
    "ObservationRequirement",
    "OptionFilter",
    "OptionRight",
    "Options",
    "Provider",
    "ProviderPreference",
    "ExpiryRange",
    "StrikeRange",
]
