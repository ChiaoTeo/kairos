from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EarnProduct:
    product_id: str
    asset: str
    product_type: str
    apr: Decimal | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    status: str = "unknown"


@dataclass(frozen=True, slots=True)
class EarnPosition:
    product_id: str
    asset: str
    principal: Decimal
    accrued_reward: Decimal = Decimal("0")
    status: str = "unknown"
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EarnReward:
    asset: str
    amount: Decimal
    product_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EarnSubscribeRequest:
    product_id: str
    amount: Decimal
    auto_renew: bool | None = None

    def __post_init__(self) -> None:
        if not self.product_id.strip() or self.amount <= 0:
            raise ValueError("Earn subscription requires a product and positive amount")


@dataclass(frozen=True, slots=True)
class EarnRedeemRequest:
    product_id: str
    amount: Decimal | None = None
    dest_account: str | None = None

    def __post_init__(self) -> None:
        if not self.product_id.strip() or (self.amount is not None and self.amount <= 0):
            raise ValueError("Earn redemption requires a product and positive amount")


__all__ = ["EarnProduct", "EarnPosition", "EarnRedeemRequest", "EarnReward", "EarnSubscribeRequest"]
