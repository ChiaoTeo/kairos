from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .events import AccountRuntimeEvent, ClockEvent, MarketEvent, RuntimeEvent, SystemRuntimeEvent


class RuntimeMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class RuntimeLine:
    mode: RuntimeMode
    items: tuple[RuntimeEvent, ...]
    preserve_order: bool = False

    def __init__(
        self,
        mode: RuntimeMode | str,
        items: Iterable[RuntimeEvent],
        *,
        preserve_order: bool = False,
    ) -> None:
        runtime_mode = mode if isinstance(mode, RuntimeMode) else RuntimeMode(str(mode))
        values = tuple(items)
        ordered = values if preserve_order else tuple(sorted(enumerate(values), key=_event_sort_key))
        if not preserve_order:
            values = tuple(item for _, item in ordered)
        object.__setattr__(self, "mode", runtime_mode)
        object.__setattr__(self, "items", values)
        object.__setattr__(self, "preserve_order", preserve_order)

    def events(self) -> Iterable[RuntimeEvent]:
        return iter(self.items)


def runtime_line(
    mode: RuntimeMode | str,
    items: Iterable[RuntimeEvent],
    *,
    preserve_order: bool = False,
) -> RuntimeLine:
    return RuntimeLine(mode, items, preserve_order=preserve_order)


def _event_sort_key(item: tuple[int, RuntimeEvent]) -> tuple[object, int, int]:
    index, event = item
    return (event.time, _event_priority(event), index)


def _event_priority(event: RuntimeEvent) -> int:
    if isinstance(event, SystemRuntimeEvent):
        return 0
    if isinstance(event, ClockEvent):
        return 5
    if isinstance(event, AccountRuntimeEvent):
        return 10
    if isinstance(event, MarketEvent):
        return 20
    return 30


__all__ = ["RuntimeLine", "RuntimeMode", "runtime_line"]
