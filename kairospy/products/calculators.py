from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from kairospy.reference.contracts import InstrumentDefinition
from kairospy.trading.product import ContractType, ProductType, option_multiplier


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
        return option_multiplier(_spec(definition))

    def market_value(self, definition, quantity, mark, average_price):
        return quantity * mark * self._multiplier(definition)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * (mark - average_price) * self._multiplier(definition)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * (exit_price - average_price) * direction * self._multiplier(definition)


class LinearContractCalculator:
    def _size(self, definition):
        return _spec(definition).contract_size

    def market_value(self, definition, quantity, mark, average_price):
        return self.unrealized_pnl(definition, quantity, mark, average_price)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * self._size(definition) * (mark - average_price)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * self._size(definition) * (exit_price - average_price) * direction


class InverseContractCalculator:
    def _value(self, definition):
        return _spec(definition).contract_size

    def market_value(self, definition, quantity, mark, average_price):
        return self.unrealized_pnl(definition, quantity, mark, average_price)

    def unrealized_pnl(self, definition, quantity, mark, average_price):
        return quantity * self._value(definition) * (Decimal("1") / average_price - Decimal("1") / mark)

    def realized_pnl(self, definition, closing_quantity, exit_price, average_price, direction):
        return closing_quantity * self._value(definition) * (Decimal("1") / average_price - Decimal("1") / exit_price) * direction


class QuantoContractCalculator(LinearContractCalculator):
    def _size(self, definition):
        spec = _spec(definition)
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
        product_type = _product_type(definition)
        if product_type in {ProductType.EQUITY, ProductType.ETF, ProductType.CRYPTO_SPOT, ProductType.TOKENIZED_EQUITY, ProductType.INDEX}:
            return self.spot
        if product_type in {ProductType.LISTED_OPTION, ProductType.CRYPTO_OPTION}:
            return self.option
        if product_type in {ProductType.FUTURE, ProductType.PERPETUAL}:
            if _spec(definition).contract_type is ContractType.INVERSE:
                return self.inverse
            if _spec(definition).contract_type is ContractType.QUANTO:
                return self.quanto
            return self.linear
        raise ValueError(f"no position calculator for {product_type}")


def _spec(definition):
    return definition.contract_spec


def _product_type(definition):
    return definition.instrument_type
