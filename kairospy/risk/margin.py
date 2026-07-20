from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MarginResult:
    initial_margin: Decimal
    maintenance_margin: Decimal
    available_after: Decimal
    leverage: Decimal
    liquidation_price: Decimal | None


class MarginPolicy(Protocol):
    def calculate(self, *, equity: Decimal, quantity: Decimal, price: Decimal, contract_size: Decimal = Decimal("1"), leverage: Decimal = Decimal("1"), direction: int = 1) -> MarginResult: ...


class SecuritiesCashPolicy:
    def calculate(self, *, equity, quantity, price, contract_size=Decimal("1"), leverage=Decimal("1"), direction=1):
        required = max(Decimal("0"), quantity * price * contract_size * direction)
        return MarginResult(required, required, equity - required, Decimal("1"), None)


@dataclass(frozen=True, slots=True)
class SecuritiesMarginApproximationPolicy:
    initial_rate: Decimal = Decimal("0.50")
    maintenance_rate: Decimal = Decimal("0.25")

    def calculate(self, *, equity, quantity, price, contract_size=Decimal("1"), leverage=Decimal("1"), direction=1):
        notional = abs(quantity * price * contract_size)
        initial, maintenance = notional * self.initial_rate, notional * self.maintenance_rate
        return MarginResult(initial, maintenance, equity - initial, Decimal("1") / self.initial_rate, None)


class CryptoSpotPolicy(SecuritiesCashPolicy):
    """Fully funded spot policy; short spot positions are not permitted."""

    def calculate(self, *, equity, quantity, price, contract_size=Decimal("1"), leverage=Decimal("1"), direction=1):
        if direction < 0:
            raise ValueError("spot policy does not permit uncovered shorts")
        return super().calculate(equity=equity, quantity=quantity, price=price, contract_size=contract_size, leverage=Decimal("1"), direction=direction)


@dataclass(frozen=True, slots=True)
class CryptoDerivativesPolicy:
    maintenance_rate: Decimal = Decimal("0.005")

    def calculate(self, *, equity, quantity, price, contract_size=Decimal("1"), leverage=Decimal("1"), direction=1):
        if leverage <= 0:
            raise ValueError("leverage must be positive")
        notional = abs(quantity * price * contract_size)
        initial = notional / leverage
        maintenance = notional * self.maintenance_rate
        liquidation = price * (Decimal("1") - Decimal(direction) / leverage + Decimal(direction) * self.maintenance_rate)
        return MarginResult(initial, maintenance, equity - initial, leverage, max(Decimal("0"), liquidation))


class CryptoCrossMarginPolicy(CryptoDerivativesPolicy):
    """Cross-margin approximation using total account equity."""

    def calculate(self, **kwargs):
        return super().calculate(**kwargs)


class CryptoIsolatedMarginPolicy(CryptoDerivativesPolicy):
    """Isolated-margin approximation; equity is the isolated collateral only."""

    def calculate(self, **kwargs):
        result = super().calculate(**kwargs)
        if result.available_after < 0:
            raise ValueError("isolated collateral is insufficient")
        return result


@dataclass(frozen=True, slots=True)
class VenueSpecificDerivativesPolicy(CryptoDerivativesPolicy):
    """Conservative local overlay for a Venue-provided maintenance schedule."""

    initial_margin_floor_rate: Decimal = Decimal("0.10")

    def calculate(self, *, equity, quantity, price, contract_size=Decimal("1"), leverage=Decimal("1"), direction=1):
        result = super().calculate(
            equity=equity, quantity=quantity, price=price, contract_size=contract_size,
            leverage=leverage, direction=direction,
        )
        notional = abs(quantity * price * contract_size)
        conservative_initial = max(result.initial_margin, notional * self.initial_margin_floor_rate)
        return MarginResult(
            conservative_initial, result.maintenance_margin, equity - conservative_initial,
            result.leverage, result.liquidation_price,
        )


@dataclass(frozen=True, slots=True)
class DefinedRiskOptionPolicy:
    def calculate_spread(self, *, width: Decimal, net_credit: Decimal, multiplier: Decimal, quantity: Decimal, equity: Decimal) -> MarginResult:
        initial = max(Decimal("0"), (width - net_credit) * multiplier * quantity)
        return MarginResult(initial, initial, equity - initial, Decimal("1"), None)
