from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, AssetCode, Environment, ProductFamily


@dataclass(frozen=True, slots=True)
class InitialAssetBalance:
    """An initial asset quantity for a simulated account."""

    asset: AssetCode | str
    quantity: Decimal

    def __post_init__(self) -> None:
        asset = self.asset if isinstance(self.asset, AssetCode) else AssetCode(self.asset)
        quantity = Decimal(str(self.quantity))
        if quantity < 0:
            raise ValueError("initial balance quantity cannot be negative")
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "quantity", quantity)


@dataclass(frozen=True, slots=True)
class SimulatedAccount:
    account_id: str
    initial_balances: tuple[InitialAssetBalance, ...] = ()
    broker: str = "simulated"
    environment: Environment = Environment.BACKTEST
    product_family: ProductFamily = ProductFamily.SPOT
    fee_rate: Decimal = Decimal("0")
    price_field: str = "close"

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("simulated account_id is required")
        if self.fee_rate < 0:
            raise ValueError("fee_rate cannot be negative")
        assets = tuple(self.initial_balances)
        if len({item.asset for item in assets}) != len(assets):
            raise ValueError("initial balances cannot contain duplicate assets")
        object.__setattr__(self, "initial_balances", assets)

    @property
    def settlement_asset(self) -> AssetCode:
        """Default execution settlement asset, independent from account balances."""

        return self.initial_balances[0].asset if self.initial_balances else AssetCode("USD")

    @property
    def valuation_asset(self) -> AssetCode:
        """Default display valuation asset; it is not the account's only asset."""

        return self.settlement_asset

    @property
    def context(self) -> AccountRuntimeContext:
        return AccountRuntimeContext(AccountSegment(self.broker, self.account_id, AccountModel.NO_MARGIN, self.product_family), self.environment)


__all__ = ["InitialAssetBalance", "SimulatedAccount"]
