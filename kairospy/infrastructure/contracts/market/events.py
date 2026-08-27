"""Market events backed only by the owner native contract."""

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract

if TYPE_CHECKING:
    from kairospy._native_market_contract import (
        MarketEvent,
        MarketInvalidEventError,
    )


def _native() -> Any:
    return load_owner_contract("Market")


def decode_event(payload: bytes) -> MarketEvent:
    return _native().decode_event(payload)


if not TYPE_CHECKING:
    MarketEvent = _native().MarketEvent
    MarketInvalidEventError = _native().MarketInvalidEventError


__all__ = ["MarketEvent", "MarketInvalidEventError", "decode_event"]
