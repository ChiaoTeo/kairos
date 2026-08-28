from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import ClassVar, Literal, Protocol, Self, runtime_checkable


MAX_DECIMAL_SCALE = 18
_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1
_DECIMAL_TEXT = re.compile(r"-?\d+(?:\.\d*)?\Z")


@runtime_checkable
class DecimalValue(Protocol):
    """Read-only exact decimal shape shared by native and Python values."""

    @property
    def semantic_type(self) -> str: ...

    @property
    def value(self) -> Decimal: ...

    @property
    def mantissa(self) -> int: ...

    @property
    def scale(self) -> int: ...


@runtime_checkable
class QuantityLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["quantity"]: ...


@runtime_checkable
class SignedQuantityLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["signed_quantity"]: ...


@runtime_checkable
class PriceLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["price"]: ...


@runtime_checkable
class PriceDeltaLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["price_delta"]: ...


@runtime_checkable
class MoneyLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["money"]: ...


@runtime_checkable
class RateLike(DecimalValue, Protocol):
    @property
    def semantic_type(self) -> Literal["rate"]: ...


@dataclass(frozen=True, slots=True, init=False)
class _FixedDecimal:
    """Shared mechanics for exact semantic decimals.

    This base is private because decimal representation alone carries no
    business meaning. Public values must select one of the semantic subclasses.
    """

    value: Decimal
    _allow_negative: ClassVar[bool] = True
    _allow_zero: ClassVar[bool] = True
    _semantic_type: ClassVar[str]

    def __init__(self, value: Decimal | str | int | DecimalValue | Self) -> None:
        if isinstance(value, _FixedDecimal):
            if type(value) is not type(self):
                raise TypeError(
                    f"{type(self).__name__} cannot be constructed from "
                    f"{type(value).__name__}"
                )
            decimal = value.value
        elif isinstance(value, DecimalValue):
            if value.semantic_type != self._semantic_type:
                raise TypeError(
                    f"{type(self).__name__} cannot be constructed from "
                    f"{value.semantic_type}"
                )
            decimal = value.value
        else:
            decimal = _decimal_input(value, type(self).__name__)
        mantissa, scale = _normalized_parts(decimal, type(self).__name__)
        if not self._allow_negative and mantissa < 0:
            raise ValueError(f"{type(self).__name__} cannot be negative")
        if not self._allow_zero and mantissa == 0:
            raise ValueError(f"{type(self).__name__} must be positive")
        object.__setattr__(self, "value", Decimal(mantissa).scaleb(-scale))

    @property
    def semantic_type(self) -> str:
        return self._semantic_type

    @property
    def mantissa(self) -> int:
        return _normalized_parts(self.value, type(self).__name__)[0]

    @property
    def scale(self) -> int:
        return _normalized_parts(self.value, type(self).__name__)[1]

    def __str__(self) -> str:
        return format(self.value, "f")

    def __format__(self, format_spec: str) -> str:
        return format(self.value, format_spec)

    def __lt__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, _FixedDecimal)
        return self.value < other.value

    def __le__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, _FixedDecimal)
        return self.value <= other.value

    def __gt__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, _FixedDecimal)
        return self.value > other.value

    def __ge__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, _FixedDecimal)
        return self.value >= other.value


@dataclass(frozen=True, slots=True, init=False)
class Quantity(_FixedDecimal):
    """A non-negative asset or order quantity."""

    _allow_negative: ClassVar[bool] = False
    _semantic_type: ClassVar[Literal["quantity"]] = "quantity"

    @property
    def semantic_type(self) -> Literal["quantity"]:
        return "quantity"

    @classmethod
    def positive(cls, value: Decimal | str | int | QuantityLike | Self) -> Self:
        result = cls(value)
        if result.is_zero:
            raise ValueError("Quantity must be positive")
        return result

    @property
    def is_zero(self) -> bool:
        return self.mantissa == 0

    @property
    def is_positive(self) -> bool:
        return self.mantissa > 0

    def checked_add(self, other: QuantityLike) -> Quantity:
        _require_semantic(other, "quantity", "Quantity")
        return Quantity(self.value + other.value)

    def checked_sub(self, other: QuantityLike) -> Quantity:
        _require_semantic(other, "quantity", "Quantity")
        return Quantity(self.value - other.value)

    def is_multiple_of(self, increment: QuantityLike) -> bool:
        _require_semantic(increment, "quantity", "Quantity")
        if increment.mantissa == 0:
            raise ValueError("Quantity increment cannot be zero")
        return self.value % increment.value == 0

    def checked_mul(self, price: PriceLike) -> Money:
        _require_semantic(price, "price", "Price")
        return Money(self.value * price.value)

    def __add__(self, other: QuantityLike) -> Quantity:
        return self.checked_add(other)

    def __sub__(self, other: QuantityLike) -> Quantity:
        return self.checked_sub(other)

    def __mul__(self, other: PriceLike) -> Money:
        return self.checked_mul(other)


@dataclass(frozen=True, slots=True, init=False)
class SignedQuantity(_FixedDecimal):
    """A signed position, inventory quantity, or balance delta."""

    _semantic_type: ClassVar[Literal["signed_quantity"]] = "signed_quantity"

    @property
    def semantic_type(self) -> Literal["signed_quantity"]:
        return "signed_quantity"

    @property
    def is_zero(self) -> bool:
        return self.mantissa == 0

    @property
    def is_positive(self) -> bool:
        return self.mantissa > 0

    @property
    def is_negative(self) -> bool:
        return self.mantissa < 0

    def checked_neg(self) -> SignedQuantity:
        return SignedQuantity(-self.value)

    def checked_add(self, other: SignedQuantityLike) -> SignedQuantity:
        _require_semantic(other, "signed_quantity", "SignedQuantity")
        return SignedQuantity(self.value + other.value)

    def checked_sub(self, other: SignedQuantityLike) -> SignedQuantity:
        _require_semantic(other, "signed_quantity", "SignedQuantity")
        return SignedQuantity(self.value - other.value)

    def checked_mul(self, price: PriceLike) -> Money:
        _require_semantic(price, "price", "Price")
        return Money(self.value * price.value)

    def __neg__(self) -> SignedQuantity:
        return self.checked_neg()

    def __add__(self, other: SignedQuantityLike) -> SignedQuantity:
        return self.checked_add(other)

    def __sub__(self, other: SignedQuantityLike) -> SignedQuantity:
        return self.checked_sub(other)

    def __mul__(self, other: PriceLike) -> Money:
        return self.checked_mul(other)


@dataclass(frozen=True, slots=True, init=False)
class Price(_FixedDecimal):
    """A strictly positive price."""

    _allow_negative: ClassVar[bool] = False
    _allow_zero: ClassVar[bool] = False
    _semantic_type: ClassVar[Literal["price"]] = "price"

    @property
    def semantic_type(self) -> Literal["price"]:
        return "price"

    @property
    def is_positive(self) -> bool:
        return True

    def is_multiple_of(self, increment: PriceLike) -> bool:
        _require_semantic(increment, "price", "Price")
        return self.value % increment.value == 0

    def checked_sub(self, other: PriceLike) -> PriceDelta:
        _require_semantic(other, "price", "Price")
        return PriceDelta(self.value - other.value)

    def checked_mul(self, quantity: QuantityLike) -> Money:
        _require_semantic(quantity, "quantity", "Quantity")
        return Money(self.value * quantity.value)

    def checked_div(self, quantity: QuantityLike) -> Price:
        _require_semantic(quantity, "quantity", "Quantity")
        if quantity.mantissa == 0:
            raise ValueError("division by zero")
        return Price(self.value / quantity.value)

    def __sub__(self, other: PriceLike) -> PriceDelta:
        return self.checked_sub(other)

    def __mul__(self, other: QuantityLike) -> Money:
        return self.checked_mul(other)


@dataclass(frozen=True, slots=True, init=False)
class PriceDelta(_FixedDecimal):
    """A signed difference between two prices."""

    _semantic_type: ClassVar[Literal["price_delta"]] = "price_delta"

    @property
    def semantic_type(self) -> Literal["price_delta"]:
        return "price_delta"

    def checked_mul(self, quantity: SignedQuantityLike) -> Money:
        _require_semantic(quantity, "signed_quantity", "SignedQuantity")
        return Money(self.value * quantity.value)

    def __mul__(self, other: SignedQuantityLike) -> Money:
        return self.checked_mul(other)


@dataclass(frozen=True, slots=True, init=False)
class Money(_FixedDecimal):
    """A signed amount of money, equity, margin, fee, or profit and loss."""

    _semantic_type: ClassVar[Literal["money"]] = "money"

    @property
    def semantic_type(self) -> Literal["money"]:
        return "money"

    @property
    def is_zero(self) -> bool:
        return self.mantissa == 0

    @property
    def is_negative(self) -> bool:
        return self.mantissa < 0

    def checked_neg(self) -> Money:
        return Money(-self.value)

    def checked_add(self, other: MoneyLike) -> Money:
        _require_semantic(other, "money", "Money")
        return Money(self.value + other.value)

    def checked_sub(self, other: MoneyLike) -> Money:
        _require_semantic(other, "money", "Money")
        return Money(self.value - other.value)

    def checked_div(self, quantity: SignedQuantityLike) -> Price:
        _require_semantic(quantity, "signed_quantity", "SignedQuantity")
        if quantity.mantissa == 0:
            raise ValueError("division by zero")
        return Price(self.value / quantity.value)

    def __neg__(self) -> Money:
        return self.checked_neg()

    def __add__(self, other: MoneyLike) -> Money:
        return self.checked_add(other)

    def __sub__(self, other: MoneyLike) -> Money:
        return self.checked_sub(other)

    def __truediv__(self, other: SignedQuantityLike) -> Price:
        return self.checked_div(other)


@dataclass(frozen=True, slots=True, init=False)
class Rate(_FixedDecimal):
    """A signed exact decimal rate."""

    _semantic_type: ClassVar[Literal["rate"]] = "rate"

    @property
    def semantic_type(self) -> Literal["rate"]:
        return "rate"

    def checked_add(self, other: RateLike) -> Rate:
        _require_semantic(other, "rate", "Rate")
        return Rate(self.value + other.value)

    def checked_sub(self, other: RateLike) -> Rate:
        _require_semantic(other, "rate", "Rate")
        return Rate(self.value - other.value)

    def __add__(self, other: RateLike) -> Rate:
        return self.checked_add(other)

    def __sub__(self, other: RateLike) -> Rate:
        return self.checked_sub(other)


def _decimal_input(value: Decimal | str | int | DecimalValue, type_name: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{type_name} requires an exact decimal value")
    if isinstance(value, Decimal):
        decimal = value
    elif isinstance(value, int):
        decimal = Decimal(value)
    elif isinstance(value, str):
        if _DECIMAL_TEXT.fullmatch(value) is None:
            raise ValueError(f"{type_name} requires canonical decimal text")
        decimal = Decimal(value)
    else:
        raise TypeError(f"{type_name} requires Decimal, canonical text, or int")
    if not decimal.is_finite():
        raise ValueError(f"{type_name} must be finite")
    return decimal


def _normalized_parts(value: Decimal, type_name: str) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError(f"{type_name} must be finite")
    mantissa = 0
    for digit in digits:
        mantissa = mantissa * 10 + digit
    if sign:
        mantissa = -mantissa
    if exponent >= 0:
        mantissa *= 10**exponent
        scale = 0
    else:
        scale = -exponent
    if mantissa == 0:
        return 0, 0
    while scale > 0 and mantissa % 10 == 0:
        mantissa //= 10
        scale -= 1
    if scale > MAX_DECIMAL_SCALE:
        raise ValueError(f"{type_name} scale exceeds {MAX_DECIMAL_SCALE} digits")
    if not _I64_MIN <= mantissa <= _I64_MAX:
        raise ValueError(f"{type_name} coefficient exceeds i64")
    return mantissa, scale


def _require_semantic(
    value: DecimalValue, semantic_type: str, type_name: str
) -> None:
    if not isinstance(value, DecimalValue) or value.semantic_type != semantic_type:
        raise TypeError(f"operation requires {type_name}")


__all__ = [
    "DecimalValue",
    "MAX_DECIMAL_SCALE",
    "Money",
    "MoneyLike",
    "Price",
    "PriceDelta",
    "PriceDeltaLike",
    "PriceLike",
    "Quantity",
    "QuantityLike",
    "Rate",
    "RateLike",
    "SignedQuantity",
    "SignedQuantityLike",
]
