from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True, slots=True, order=True)
class AssetId:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if not normalized:
            raise ValueError("asset id cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Amount:
    asset: AssetId
    quantity: Decimal


@dataclass(frozen=True, slots=True, order=True)
class VenueId:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not normalized:
            raise ValueError("venue id cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class InstrumentId:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized:
            raise ValueError("instrument id cannot be empty")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class AccountType(StrEnum):
    SECURITIES_CASH = "securities_cash"
    SECURITIES_MARGIN = "securities_margin"
    CRYPTO_SPOT = "crypto_spot"
    CROSS_MARGIN = "cross_margin"
    ISOLATED_MARGIN = "isolated_margin"
    DERIVATIVES = "derivatives"
    SUB_ACCOUNT = "sub_account"


@dataclass(frozen=True, slots=True, order=True)
class AccountKey:
    venue_id: VenueId
    account_id: str
    account_type: AccountType

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("account id cannot be empty")

    @property
    def value(self) -> str:
        return f"{self.venue_id}:{self.account_type}:{self.account_id}"
