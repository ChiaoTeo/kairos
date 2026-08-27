"""Account event exports backed only by the owner native contract."""

from typing import Any

from kairospy.infrastructure.contracts._native import load_owner_contract


def _native() -> Any:
    return load_owner_contract("Account")


def decode_event(payload: bytes) -> object:
    return _native().decode_event(payload)


AccountEvent = _native().AccountEvent


__all__ = ["AccountEvent", "decode_event"]
