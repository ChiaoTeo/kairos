"""Common Python contract transport primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    """Stable write-side envelope shared by module command facades."""

    command_type: str
    request_id: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class QueryEnvelope:
    """Stable read-side query envelope for low-frequency query transports."""

    query_type: str
    request_id: str
    payload: bytes


__all__ = [
    "CommandEnvelope",
    "QueryEnvelope",
]
