"""Reference events backed only by the owner-native contract."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kairospy._native_reference_contract import (
        ReferenceEvent,
        ReferenceInvalidEventError,
    )


def _native() -> Any:
    module = import_module("kairospy._native_reference_contract")
    info = module.build_info()
    if info.api_version != 1 or info.owner != "Reference":
        raise ImportError("kairospy Reference native contract ABI mismatch")
    return module


def decode_event(payload: bytes) -> ReferenceEvent:
    return _native().decode_event(payload)


if not TYPE_CHECKING:
    ReferenceEvent = _native().ReferenceEvent
    ReferenceInvalidEventError = _native().ReferenceInvalidEventError


__all__ = ["ReferenceEvent", "ReferenceInvalidEventError", "decode_event"]
