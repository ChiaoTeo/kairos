from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class OrderQueryResult:
    account: str
    orders: tuple[object, ...]
    count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "count", len(self.orders))


@dataclass(frozen=True, slots=True)
class OrderActionResult:
    dry_run: bool
    request: Mapping[str, object]
    result: object | None = None


@dataclass(frozen=True, slots=True)
class OrderRuntimeResult:
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OrderJournalResult:
    account: str
    order_id: str
    journal: str
    records: tuple[Mapping[str, object], ...]
    count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "count", len(self.records))


__all__ = ["OrderActionResult", "OrderJournalResult", "OrderQueryResult", "OrderRuntimeResult"]
