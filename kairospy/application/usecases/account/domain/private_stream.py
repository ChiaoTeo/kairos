from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


PrivateStreamUpdateKey = tuple[str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class PrivateStreamCheckpoint:
    seen_order_updates: tuple[PrivateStreamUpdateKey, ...] = ()
    seen_trade_updates: tuple[PrivateStreamUpdateKey, ...] = ()
    order_timestamps: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "seen_order_updates": [list(item) for item in sorted(self.seen_order_updates)],
            "seen_trade_updates": [list(item) for item in sorted(self.seen_trade_updates)],
            "order_timestamps": {str(key): str(value) for key, value in sorted(self.order_timestamps.items())},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PrivateStreamCheckpoint":
        timestamps = value.get("order_timestamps")
        return cls(
            seen_order_updates=tuple(_tuple_key(item, 5) for item in _list(value.get("seen_order_updates"))),
            seen_trade_updates=tuple(_tuple_key(item, 5) for item in _list(value.get("seen_trade_updates"))),
            order_timestamps={str(key): str(item) for key, item in timestamps.items()} if isinstance(timestamps, Mapping) else {},
        )


def _list(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, list) else ()


def _tuple_key(value: object, size: int) -> PrivateStreamUpdateKey:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError("private stream checkpoint key has invalid shape")
    return tuple(str(item) for item in value)  # type: ignore[return-value]


__all__ = ["PrivateStreamCheckpoint", "PrivateStreamUpdateKey"]
