from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT

from kairospy.data.contracts import DatasetKey, DatasetLike
from kairospy.data.storage.client import DatasetClient


@dataclass(frozen=True, slots=True)
class ConsolidatedTradeInput:
    dataset: DatasetLike
    provider: str
    venue: str
    instrument_type: str
    quote_currency: str
    price_field: str = "price"
    size_field: str = "size"


@dataclass(frozen=True, slots=True)
class ConsolidatedTradePolicy:
    policy_id: str
    version: str
    target_currency: str
    fx_to_target: dict[str, Decimal]

    def __post_init__(self) -> None:
        if not self.policy_id or not self.version or not self.target_currency:
            raise ValueError("consolidation policy identity and target currency are required")
        if any(value <= 0 for value in self.fx_to_target.values()):
            raise ValueError("currency conversion rates must be positive")


class ConsolidatedTradeBuilder:
    """Build an explicit cross-venue product; never acts as source fallback."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.root, self.data = Path(root), DatasetClient(root)

    def build(self, output_key: DatasetKey | str, title: str, inputs: tuple[ConsolidatedTradeInput, ...],
              policy: ConsolidatedTradePolicy, *, start, end):
        raise RuntimeError("curated release publishing has been removed; write curated datasets through DatasetWriter")
