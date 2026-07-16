from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from trading.domain.instrument import InstrumentDefinition
from trading.domain.product import ContractType, FutureSpec, ListedOptionSpec, PerpetualSpec, ProductType


class PositionCalculator(Protocol):
    def market_value(self, definition: InstrumentDefinition, quantity: Decimal, mark: Decimal, average_price: Decimal) -> Decimal: ...
    def unrealized_pnl(self, definition: InstrumentDefinition, quantity: Decimal, mark: Decimal, average_price: Decimal) -> Decimal: ...
    def realized_pnl(self, definition: InstrumentDefinition, closing_quantity: Decimal, exit_price: Decimal, average_price: Decimal, direction: int) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class SpotCalculator:
    multiplier: Decimal = Decimal("1")

    def market_value(self, definition, quantity, mark, average_price):
        return quantity * mark * self.multiplier

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * (mark - average_price) * self.multiplier

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * (exit_price - average_price) * direction * self.multiplier


class OptionCalculator:
    def _multiplier(self, definition):
        spec = definition.product_spec
        return spec.multiplier if isinstance(spec, ListedOptionSpec) else spec.contract_size

    def market_value(self, definition, quantity, mark, average_price):
        return quantity * mark * self._multiplier(definition)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * (mark - average_price) * self._multiplier(definition)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * (exit_price - average_price) * direction * self._multiplier(definition)


class LinearContractCalculator:
    def _size(self, definition):
        return definition.product_spec.contract_size

    def market_value(self, definition, quantity, mark, average_price):
        return self.unrealized_pnl(definition, quantity, mark, average_price)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * self._size(definition) * (mark - average_price)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * self._size(definition) * (exit_price - average_price) * direction


class InverseContractCalculator:
    def _value(self, definition):
        return definition.product_spec.contract_size

    def market_value(self, definition, quantity, mark, average_price):
        return self.unrealized_pnl(definition, quantity, mark, average_price)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * self._value(definition) * (Decimal("1") / average_price - Decimal("1") / mark)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * self._value(definition) * (Decimal("1") / average_price - Decimal("1") / exit_price) * direction


class QuantoContractCalculator(LinearContractCalculator):
    def _size(self, definition):
        spec = definition.product_spec
        if spec.quanto_multiplier is None or spec.quanto_multiplier <= 0:
            raise ValueError("quanto contract requires a positive quanto_multiplier")
        return spec.contract_size * spec.quanto_multiplier


class PositionCalculatorRegistry:
    def __init__(self) -> None:
        self.spot = SpotCalculator()
        self.option = OptionCalculator()
        self.linear = LinearContractCalculator()
        self.inverse = InverseContractCalculator()
        self.quanto = QuantoContractCalculator()

    def for_definition(self, definition: InstrumentDefinition) -> PositionCalculator:
        if definition.product_type in {ProductType.EQUITY, ProductType.ETF, ProductType.CRYPTO_SPOT, ProductType.TOKENIZED_EQUITY, ProductType.INDEX}:
            return self.spot
        if definition.product_type in {ProductType.LISTED_OPTION, ProductType.CRYPTO_OPTION}:
            return self.option
        if definition.product_type in {ProductType.FUTURE, ProductType.PERPETUAL}:
            if definition.product_spec.contract_type is ContractType.INVERSE:
                return self.inverse
            if definition.product_spec.contract_type is ContractType.QUANTO:
                return self.quanto
            return self.linear
        raise ValueError(f"no position calculator for {definition.product_type}")
