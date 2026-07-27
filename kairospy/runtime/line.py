from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .data import RuntimeDataEnvelope


class RuntimeMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class RuntimeLine:
    mode: RuntimeMode
    items: tuple[RuntimeDataEnvelope, ...]
    preserve_order: bool = False

    def __init__(
        self,
        mode: RuntimeMode | str,
        items: Iterable[RuntimeDataEnvelope],
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

    def events(self) -> Iterable[RuntimeDataEnvelope]:
        return iter(self.items)


def runtime_line(
    mode: RuntimeMode | str,
    items: Iterable[RuntimeDataEnvelope],
    *,
    preserve_order: bool = False,
) -> RuntimeLine:
    return RuntimeLine(mode, items, preserve_order=preserve_order)


def _event_sort_key(item: tuple[int, RuntimeDataEnvelope]) -> tuple[object, int, int]:
    index, event = item
    return (event.time, _event_priority(event), index)


def _event_priority(event: RuntimeDataEnvelope) -> int:
    if event.domain == "system":
        return 0
    if event.domain == "clock":
        return 5
    if event.domain == "account":
        return 10
    if event.domain == "market":
        return 20
    return 30


__all__ = ["RuntimeLine", "RuntimeMode", "runtime_line"]
