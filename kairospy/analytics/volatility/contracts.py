from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from kairospy.identity import InstrumentId
from kairospy.reference.contracts import OptionRight


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VolObservation:
    instrument_id: InstrumentId
    underlying_id: InstrumentId
    as_of: datetime
    expiry: datetime
    strike: Decimal
    forward: Decimal
    time_to_expiry: Decimal
    right: OptionRight
    market_price: Decimal
    implied_volatility: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    source: str = "internal"

    @property
    def log_moneyness(self) -> Decimal:
        from math import log
        return Decimal(str(log(float(self.strike / self.forward))))

    @property
    def total_variance(self) -> Decimal:
        return self.implied_volatility * self.implied_volatility * self.time_to_expiry


@dataclass(frozen=True, slots=True)
class SviParameters:
    a: Decimal
    b: Decimal
    rho: Decimal
    m: Decimal
    sigma: Decimal

    def __post_init__(self) -> None:
        if self.b < 0 or not Decimal("-1") < self.rho < Decimal("1") or self.sigma <= 0:
            raise ValueError("invalid SVI parameters")


@dataclass(frozen=True, slots=True)
class SmileCalibration:
    expiry: datetime
    time_to_expiry: Decimal
    forward: Decimal
    parameters: SviParameters | None
    status: CalibrationStatus
    observation_count: int
    root_mean_squared_error: Decimal | None


@dataclass(frozen=True, slots=True)
class ArbitrageDiagnostics:
    non_positive_variance: tuple[str, ...] = ()
    butterfly_violations: tuple[str, ...] = ()
    calendar_violations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not (self.non_positive_variance or self.butterfly_violations or self.calendar_violations)


@dataclass(frozen=True, slots=True)
class SurfaceSnapshot:
    surface_id: str
    underlying_id: InstrumentId
    as_of: datetime
    model: str
    model_version: str
    input_hash: str
    smiles: tuple[SmileCalibration, ...]
    diagnostics: ArbitrageDiagnostics
    calibration_status: CalibrationStatus = CalibrationStatus.FAILED
    rejected_observations: tuple[str, ...] = ()
    available_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("surface snapshot as_of must be timezone-aware")
        if self.available_time is None:
            object.__setattr__(self, "available_time", self.as_of)
        if self.available_time is not None and self.available_time.tzinfo is None:
            raise ValueError("surface snapshot available_time must be timezone-aware")
        if self.available_time is not None and self.available_time < self.as_of:
            raise ValueError("surface snapshot available_time cannot precede as_of")
