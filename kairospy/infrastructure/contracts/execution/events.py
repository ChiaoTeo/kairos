"""Execution events backed only by the owner-native contract."""

from typing import TYPE_CHECKING, Any

from kairospy.infrastructure.contracts._native import load_owner_contract

if TYPE_CHECKING:
    from kairospy._native_execution_contract import (
        ExecutionEvent,
        ExecutionInvalidEventError,
    )


def _native() -> Any:
    return load_owner_contract("Execution")


def decode_event(payload: bytes) -> ExecutionEvent:
    return _native().decode_event(payload)


if not TYPE_CHECKING:
    ExecutionEvent = _native().ExecutionEvent
    ExecutionInvalidEventError = _native().ExecutionInvalidEventError


__all__ = ["ExecutionEvent", "ExecutionInvalidEventError", "decode_event"]
