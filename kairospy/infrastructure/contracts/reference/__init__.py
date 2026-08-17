"""Python implementation of the Reference v2 cross-process contract.

Reference exposes typed event payloads, contract-owned SQLite queries, and
REST control commands. Business callers never know its persistence schema.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ReferenceClient
    from .control import ReferenceControlClient
    from .events import decode_event


def __getattr__(name: str):
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
    "decode_event",
]
