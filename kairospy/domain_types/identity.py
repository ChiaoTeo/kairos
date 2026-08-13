from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Id:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class InstrumentId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class ListingId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class MarketId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class AccountId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class SegmentKey(_Id):
    """Stable Account segment identity shared across application boundaries."""


@dataclass(frozen=True, slots=True)
class IntentId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class OrderId(_Id):
    pass


@dataclass(frozen=True, slots=True)
class FillId(_Id):
    pass
