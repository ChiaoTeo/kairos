from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading.pricing.service import ValuationSnapshot


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    as_of: datetime
    average_implied_vol: Decimal | None
    minimum_implied_vol: Decimal | None
    maximum_implied_vol: Decimal | None
    calibrated_surface: bool
    observation_count: int
    iv_rank: Decimal | None = None
    iv_percentile: Decimal | None = None
    put_skew: Decimal | None = None
    term_structure: Decimal | None = None
    average_iv_change: Decimal | None = None


def build_features(valuation: ValuationSnapshot, history: tuple[Decimal, ...] = ()) -> FeatureSnapshot:
    values = [item.implied_vol.volatility for item in valuation.instruments if item.implied_vol.volatility is not None]
    average = sum(values, Decimal("0")) / Decimal(len(values)) if values else None
    calibrated = bool(valuation.surface and any(item.parameters is not None for item in valuation.surface.smiles))
    series = (*history, average) if average is not None else history
    iv_rank = None
    iv_percentile = None
    if average is not None and series:
        low, high = min(series), max(series)
        iv_rank = (average - low) / (high - low) if high > low else Decimal("0.5")
        iv_percentile = Decimal(sum(value <= average for value in series)) / Decimal(len(series))
    ordered = sorted(
        (item for item in valuation.instruments if item.implied_vol.volatility is not None),
        key=lambda item: item.inputs.strike,
    )
    put_skew = ordered[0].implied_vol.volatility - ordered[-1].implied_vol.volatility if len(ordered) >= 2 else None
    by_term = sorted(
        ((item.inputs.time_to_expiry, item.implied_vol.volatility) for item in ordered),
        key=lambda item: item[0],
    )
    term_structure = by_term[-1][1] - by_term[0][1] if len({item[0] for item in by_term}) >= 2 else None
    return FeatureSnapshot(
        valuation.as_of, average, min(values) if values else None, max(values) if values else None,
        calibrated, len(values), iv_rank, iv_percentile, put_skew, term_structure,
        average - history[-1] if average is not None and history else None,
    )


class FeatureEngine:
    def __init__(self) -> None:
        self._average_iv_history: list[Decimal] = []

    def update(self, valuation: ValuationSnapshot) -> FeatureSnapshot:
        snapshot = build_features(valuation, tuple(self._average_iv_history))
        if snapshot.average_implied_vol is not None:
            self._average_iv_history.append(snapshot.average_implied_vol)
        return snapshot
