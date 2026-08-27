"""Capital events backed only by the owner native contract."""

from typing import Any

from kairospy.infrastructure.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Capital")


def decode_event(payload: bytes) -> object:
    return _native().decode_event(payload)


CapitalEvent = _native().CapitalEvent


__all__ = ["CapitalEvent", "decode_event"]
