from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True, eq=False)
class ReferenceId:
    value: str

    def __post_init__(self) -> None:
        value = str(self.value).strip()
        if not value:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReferenceId):
            return type(self) is type(other) and self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False

    def __lt__(self, other: object) -> bool:
        return self.value < str(other)

    def __hash__(self) -> int:
        return hash(self.value)


class EntityId(ReferenceId):
    pass


class AssetId(ReferenceId):
    pass


class ExternalAccountId(ReferenceId):
    pass


class AccountSegmentId(ReferenceId):
    pass


class IntentId(ReferenceId):
    pass


class InstrumentId(ReferenceId):
    pass


class FinancialProductId(ReferenceId):
    pass


class ListingId(ReferenceId):
    pass


class MarketId(ReferenceId):
    pass


class MarketTypeId(ReferenceId):
    pass


class SourceSymbol(ReferenceId):
    pass


class StrategyId(ReferenceId):
    pass


class ExchangeId(ReferenceId):
    pass


class BrokerId(ReferenceId):
    pass


class ProviderId(ReferenceId):
    pass


# Stable symbolic names for the built-in reference identifiers.  These are
# values of the shared ReferenceId types, not Integration-specific enums.
ExchangeId.BINANCE = ExchangeId("binance")  # type: ignore[attr-defined]
ExchangeId.OKX = ExchangeId("okx")  # type: ignore[attr-defined]
ExchangeId.HYPERLIQUID = ExchangeId("hyperliquid")  # type: ignore[attr-defined]
BrokerId.BINANCE = BrokerId("binance")  # type: ignore[attr-defined]
BrokerId.OKX = BrokerId("okx")  # type: ignore[attr-defined]
BrokerId.IBKR = BrokerId("ibkr")  # type: ignore[attr-defined]
ProviderId.MASSIVE = ProviderId("massive")  # type: ignore[attr-defined]


def reference_slug(value: ReferenceId | str) -> str:
    text = str(value).strip().lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text)).strip("_")


__all__ = [
    "ExternalAccountId",
    "AccountSegmentId",
    "AssetId",
    "BrokerId",
    "EntityId",
    "ExchangeId",
    "IntentId",
    "InstrumentId",
    "FinancialProductId",
    "ListingId",
    "MarketId",
    "MarketTypeId",
    "ProviderId",
    "ReferenceId",
    "SourceSymbol",
    "StrategyId",
    "reference_slug",
]
