"""Python facade for the Market v2 owner contract."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "MarketClient": (".types", "MarketClient"),
    "MarketControlClient": (".types", "MarketControlClient"),
    "MarketCurrentView": (".types", "MarketCurrentView"),
    "MarketQuoteCurrent": (".view", "MarketQuoteCurrent"),
    "MarketViewKey": (".view", "MarketViewKey"),
    "MarketViewKind": (".view", "MarketViewKind"),
    "decode_event": (".events", "decode_event"),
    "decode_events": (".events", "decode_events"),
    "MarketSubscriptionRequest": (".types", "MarketSubscriptionRequest"),
    "MarketTarget": (".types", "MarketTarget"),
    "ObservationRequirement": (".types", "ObservationRequirement"),
    "OptionRight": (".types", "OptionRight"),
    "ExpiryRange": (".types", "ExpiryRange"),
    "StrikeRange": (".types", "StrikeRange"),
    "OptionFilter": (".types", "OptionFilter"),
    "Options": (".types", "Options"),
    "Provider": (".types", "Provider"),
    "ProviderPreference": (".types", "ProviderPreference"),
    "MarketHealthResponse": (".types", "MarketHealthResponse"),
    "MarketDataRoute": (".types", "MarketDataRoute"),
    "MarketDataRoutesResponse": (".types", "MarketDataRoutesResponse"),
    "MarketSubscriptionResponse": (".types", "MarketSubscriptionResponse"),
    "MarketSubscriptionSnapshot": (".types", "MarketSubscriptionSnapshot"),
    "MarketSubscriptionsResponse": (".types", "MarketSubscriptionsResponse"),
    "MarketCommandStatus": (".types", "MarketCommandStatus"),
    "MarketReleaseOwnerResponse": (".types", "MarketReleaseOwnerResponse"),
    "MarketControlUnavailableError": (".types", "MarketControlUnavailableError"),
    "MarketControlRejectedError": (".types", "MarketControlRejectedError"),
    "MarketInvalidInputError": (".types", "MarketInvalidInputError"),
    "MarketInvalidEventError": (".types", "MarketInvalidEventError"),
    "MarketInvalidCurrentViewError": (".types", "MarketInvalidCurrentViewError"),
    "MarketCurrentViewUnavailableError": (".types", "MarketCurrentViewUnavailableError"),
}
from .events import __all__ as _EVENT_EXPORTS
_EXPORTS.update({name: (".events", name) for name in _EVENT_EXPORTS})


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(target[0], __name__), target[1])
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
