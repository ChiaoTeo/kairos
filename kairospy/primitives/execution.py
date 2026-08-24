from __future__ import annotations

from dataclasses import dataclass

from ._text import TextValue


@dataclass(frozen=True, slots=True)
class IntentId(TextValue):
    """Canonical Execution intent identity."""


@dataclass(frozen=True, slots=True)
class OrderId(TextValue):
    """Canonical exchange-facing order identity."""


@dataclass(frozen=True, slots=True)
class FillId(TextValue):
    """Canonical Execution fill identity."""


__all__ = ["FillId", "IntentId", "OrderId"]
