from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypeGuard, runtime_checkable

from .identity import AssetId, InstrumentId


class ProductType(StrEnum):
    INDEX = "index"
    EQUITY = "equity"
    ETF = "etf"
    LISTED_OPTION = "listed_option"
    CRYPTO_SPOT = "crypto_spot"
    FUTURE = "future"
    PERPETUAL = "perpetual"
    CRYPTO_OPTION = "crypto_option"
    TOKENIZED_EQUITY = "tokenized_equity"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


class ExerciseStyle(StrEnum):
    AMERICAN = "american"
    EUROPEAN = "european"


class SettlementType(StrEnum):
    CASH = "cash"
    PHYSICAL = "physical"


class SettlementSession(StrEnum):
    AM = "am"
    PM = "pm"
    CONTINUOUS = "continuous"


class ContractType(StrEnum):
    LINEAR = "linear"
    INVERSE = "inverse"
    QUANTO = "quanto"


@runtime_checkable
class OptionSpec(Protocol):
    """Common contract shared by option products across asset classes."""

    expiry: datetime
    strike: Decimal
    right: OptionRight
    exercise_style: ExerciseStyle

    @property
    def quantity_multiplier(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class IndexSpec:
    index_currency: AssetId
    primary_exchange: str | None = None


@dataclass(frozen=True, slots=True)
class EquitySpec:
    primary_exchange: str
    country: str
    trading_currency: AssetId
    settlement_cycle: str = "T+1"
    shortable: bool | None = None


@dataclass(frozen=True, slots=True)
class ListedOptionSpec:
    underlying: InstrumentId
    expiry: datetime
    strike: Decimal
    right: OptionRight
    exercise_style: ExerciseStyle
    settlement_type: SettlementType
    settlement_session: SettlementSession
    multiplier: Decimal
    last_trade_at: datetime
    exercise_threshold: Decimal = Decimal("0.01")

    @property
    def quantity_multiplier(self) -> Decimal:
        return self.multiplier


@dataclass(frozen=True, slots=True)
class CryptoSpotSpec:
    base_asset: AssetId
    quote_asset: AssetId
    minimum_notional: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FutureSpec:
    underlying_asset: AssetId
    settlement_asset: AssetId
    expiry: datetime
    contract_size: Decimal
    contract_type: ContractType
    settlement_index: str
    quanto_multiplier: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PerpetualSpec:
    underlying_asset: AssetId
    settlement_asset: AssetId
    index_id: str
    contract_size: Decimal
    contract_type: ContractType
    funding_interval_seconds: int
    quanto_multiplier: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CryptoOptionSpec:
    underlying_asset: AssetId
    quote_asset: AssetId
    settlement_asset: AssetId
    premium_asset: AssetId
    expiry: datetime
    strike: Decimal
    right: OptionRight
    exercise_style: ExerciseStyle
    contract_size: Decimal
    settlement_index: str

    @property
    def quantity_multiplier(self) -> Decimal:
        return self.contract_size


@dataclass(frozen=True, slots=True)
class TokenizedEquitySpec:
    reference_equity: InstrumentId
    token_asset: AssetId
    quote_asset: AssetId
    issuer: str


@runtime_checkable
class InstrumentContractSpec(Protocol):
    """Runtime-checkable marker for supported instrument contract specs."""


def is_option_spec(spec: object) -> TypeGuard[OptionSpec]:
    return isinstance(spec, OptionSpec)


def option_multiplier(spec: OptionSpec) -> Decimal:
    return spec.quantity_multiplier
