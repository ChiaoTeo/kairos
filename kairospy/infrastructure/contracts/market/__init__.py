"""Python implementation of the Market v2 cross-process contract.

Protocol payloads are returned as generated FlatBuffers objects.  This package
does not duplicate the tables into application-facing dataclasses.
"""

from .control import MarketControlClient
from .events import decode_event
from .view import (
    MarketViewFrame,
    MarketViewKey,
    MarketViewKind,
    MarketViewReader,
    decode_view,
)

__all__ = [
    "MarketControlClient",
    "MarketViewFrame",
    "MarketViewKey",
    "MarketViewKind",
    "MarketViewReader",
    "decode_event",
    "decode_view",
]
