from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class ParticipantSymbol(TextValue):
    """Canonical provider/participant symbol."""


@dataclass(frozen=True, slots=True)
class RemoteOrderId(TextValue):
    """Canonical provider-assigned order identity."""


ParticipantSymbolRead = NewType("ParticipantSymbolRead", str)
RemoteOrderIdRead = NewType("RemoteOrderIdRead", str)


__all__ = [
    "ParticipantSymbol",
    "ParticipantSymbolRead",
    "RemoteOrderId",
    "RemoteOrderIdRead",
]
