"""Canonical Market owner-contract types exported by the native binding."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from kairospy.contracts._native import load_owner_contract


class MarketSubscriptionResultRead(Protocol):
    @property
    def subscription_id(self) -> str: ...
    @property
    def owner_id(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def satisfied_selectors(self) -> Sequence[str]: ...
    @property
    def missing_selectors(self) -> Sequence[str]: ...
    @property
    def resolved_providers(self) -> Sequence[str]: ...
    @property
    def pending_reason(self) -> str | None: ...


class MarketReleaseResultRead(Protocol):
    @property
    def released_subscription_ids(self) -> Sequence[str]: ...


class MarketSubscriptionSnapshotRead(Protocol):
    @property
    def subscription_id(self) -> str: ...
    @property
    def owner_id(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def observations(self) -> Sequence[str]: ...
    @property
    def selected_providers(self) -> Sequence[str]: ...
    @property
    def pending_reason(self) -> str | None: ...


class MarketSubscriptionsRead(Protocol):
    @property
    def subscriptions(self) -> Sequence[MarketSubscriptionSnapshotRead]: ...


class MarketCommands(Protocol):
    def subscribe(
        self,
        request: "MarketSubscriptionRequest",
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> MarketSubscriptionResultRead: ...

    def unsubscribe(
        self,
        subscription_id: str,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> object: ...

    def release_owner(
        self,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
        launch_id: str | None = None,
    ) -> MarketReleaseResultRead: ...

    def subscriptions(
        self,
        *,
        owner_id: str | None = None,
        market_id: str | None = None,
        state: str | None = None,
    ) -> MarketSubscriptionsRead: ...


class MarketSnapshots(Protocol):
    def quote(self, scope_key: str, provider: str) -> object | None: ...
    def bar(self, scope_key: str, provider: str, qualifier: str) -> object | None: ...
    def greeks(self, scope_key: str, provider: str) -> object | None: ...
    def get(self, key: object) -> object | None: ...

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
        MarketSubscriptionSnapshot,
        MarketSubscriptionsResponse,
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
    MarketSubscriptionSnapshot = _native().MarketSubscriptionSnapshot
    MarketSubscriptionsResponse = _native().MarketSubscriptionsResponse
    MarketCommandStatus = _native().MarketCommandStatus
    MarketReleaseOwnerResponse = _native().MarketReleaseOwnerResponse
    MarketControlUnavailableError = _native().MarketControlUnavailableError
    MarketControlRejectedError = _native().MarketControlRejectedError


__all__ = [
    "MarketCommands",
    "MarketReleaseResultRead",
    "MarketSnapshots",
    "MarketSubscriptionSnapshotRead",
    "MarketSubscriptionsRead",
    "MarketSubscriptionResultRead",
    "MarketSubscriptionRequest",
    "MarketSubscriptionResponse",
    "MarketSubscriptionSnapshot",
    "MarketSubscriptionsResponse",
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
