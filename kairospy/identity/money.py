from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .assets import AssetId


@dataclass(frozen=True, slots=True)
class Amount:
    asset: AssetId
    quantity: Decimal
