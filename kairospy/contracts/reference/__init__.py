"""Python facade for the Reference v2 owner contract.

Reference exposes typed event payloads, contract-owned SQLite queries, and
REST control commands. Business callers never know its persistence schema.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from .events import __all__ as _EVENT_EXPORTS
from .results import (
    ReferenceCatalogCounts,
    ReferenceCatalogIntegrity,
    ReferenceCatalogSnapshot,
    ReferenceHealthResponse,
    ReferenceHealthStatus,
    ReferenceOptionCoverage,
    ReferenceProviderHealth,
    ReferenceProviderStatus,
    ReferenceRuntimeStatusResponse,
)

if TYPE_CHECKING:
    from kairospy._native_reference_contract import (
        ReferenceAsset,
        ReferenceExchange,
        ReferenceInstrument,
        ReferenceInstrumentAvailability,
        ReferenceInstrumentRef,
        ReferenceListing,
        ReferenceMarket,
        ReferenceTradingRules,
    )

    from .client import ReferenceClient
    from .control import ReferenceControlClient
    from .events import decode_event


def __getattr__(name: str):
    if name in _EVENT_EXPORTS:
        return getattr(import_module(".events", __name__), name)
    if name in {
        "ReferenceAsset",
        "ReferenceExchange",
        "ReferenceInstrument",
        "ReferenceInstrumentAvailability",
        "ReferenceInstrumentRef",
        "ReferenceListing",
        "ReferenceMarket",
        "ReferenceTradingRules",
    }:
        return getattr(import_module("kairospy._native_reference_contract"), name)
    if name == "ReferenceClient":
        from .client import ReferenceClient

        return ReferenceClient
    if name == "ReferenceControlClient":
        from .control import ReferenceControlClient

        return ReferenceControlClient
    if name == "decode_event":
        from .events import decode_event

        return decode_event
    raise AttributeError(name)


__all__ = [
    "ReferenceControlClient",
    "ReferenceClient",
    "ReferenceAsset",
    "ReferenceExchange",
    "ReferenceInstrument",
    "ReferenceInstrumentAvailability",
    "ReferenceInstrumentRef",
    "ReferenceListing",
    "ReferenceMarket",
    "ReferenceTradingRules",
    "ReferenceCatalogCounts",
    "ReferenceCatalogIntegrity",
    "ReferenceCatalogSnapshot",
    "ReferenceHealthResponse",
    "ReferenceHealthStatus",
    "ReferenceOptionCoverage",
    "ReferenceProviderHealth",
    "ReferenceProviderStatus",
    "ReferenceRuntimeStatusResponse",
    "decode_event",
] + _EVENT_EXPORTS
