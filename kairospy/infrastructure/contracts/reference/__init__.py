"""Python implementation of the Reference v2 cross-process contract.

Reference exposes typed event payloads and direct SQLite reads. It has no
shared-memory view capability.
"""

from .sqlite import ReferenceMarket, ReferenceSqliteReader, ReferenceWatermark


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
    "ReferenceMarket",
    "ReferenceSqliteReader",
    "ReferenceWatermark",
    "decode_event",
]
