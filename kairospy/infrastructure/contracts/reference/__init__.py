"""Python implementation of the Reference v2 cross-process contract.

Reference exposes typed event payloads, typed mmap queries, and REST control
commands. Business callers do not read its persistence database directly.
"""

from .view import ReferenceViewKey, ReferenceViewReader


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
    "ReferenceViewKey",
    "ReferenceViewReader",
    "decode_event",
]
