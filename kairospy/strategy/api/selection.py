"""Deterministic point-in-time option spread selection.

Candidates contain canonical business values. Wire and persistence numeric
representations are deliberately outside this public strategy boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.primitives.decimal import Price, Rate
from kairospy.primitives.reference import InstrumentId


_DAY_NANOS = 86_400_000_000_000


@dataclass(frozen=True, slots=True)
class OptionSelectionCandidate:
    instrument_id: InstrumentId
    reference_snapshot_id: str
    expiry_unix_nanos: int
    option_right: str
    strike: Price
    delta: Rate
    bid: Price
    ask: Price
    observed_at_unix_nanos: int
    available_at_unix_nanos: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", InstrumentId(str(self.instrument_id)))
        object.__setattr__(self, "strike", Price(self.strike))
        object.__setattr__(self, "delta", Rate(self.delta))
        object.__setattr__(self, "bid", Price(self.bid))
        object.__setattr__(self, "ask", Price(self.ask))
        if not str(self.instrument_id).strip() or not self.reference_snapshot_id.strip():
            raise ValueError("candidate identity and Reference snapshot are required")


@dataclass(frozen=True, slots=True)
class OptionSpreadSelectionRequest:
    candidates: tuple[OptionSelectionCandidate, ...]
    decision_time_unix_nanos: int
    minimum_dte: int = 30
    maximum_dte: int = 45
    short_target_delta: Rate = Rate("-0.25")
    long_target_delta: Rate = Rate("-0.10")
    maximum_quote_age_nanos: int = 60_000_000_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "short_target_delta", Rate(self.short_target_delta))
        object.__setattr__(self, "long_target_delta", Rate(self.long_target_delta))
        if not self.candidates:
            raise ValueError("option selection requires candidates")
        if self.minimum_dte < 0 or self.minimum_dte > self.maximum_dte:
            raise ValueError("option selection DTE range is invalid")
        if self.maximum_quote_age_nanos < 0:
            raise ValueError("maximum quote age cannot be negative")
        if not Decimal("-1") <= self.short_target_delta.value < Decimal("0"):
            raise ValueError("short Put target Delta must be in [-1, 0)")
        if not Decimal("-1") <= self.long_target_delta.value < Decimal("0"):
            raise ValueError("long Put target Delta must be in [-1, 0)")


@dataclass(frozen=True, slots=True)
class OptionSelectionAudit:
    instrument_id: InstrumentId
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OptionSpreadSelectionResult:
    short: OptionSelectionCandidate
    long: OptionSelectionCandidate
    audit: tuple[OptionSelectionAudit, ...]


class OptionSpreadSelectionApplication:
    """Select a protected Put spread using only facts available at decision time."""

    def select(
        self, request: OptionSpreadSelectionRequest
    ) -> OptionSpreadSelectionResult:
        eligible: list[OptionSelectionCandidate] = []
        audit: list[OptionSelectionAudit] = []
        for candidate in sorted(
            request.candidates, key=lambda item: str(item.instrument_id)
        ):
            reasons = self._rejections(candidate, request)
            audit.append(
                OptionSelectionAudit(
                    instrument_id=candidate.instrument_id,
                    accepted=not reasons,
                    reasons=tuple(reasons),
                )
            )
            if not reasons:
                eligible.append(candidate)
        if not eligible:
            raise ValueError(
                "no point-in-time option candidate satisfies selection rules"
            )

        short = min(
            eligible,
            key=lambda item: (
                abs(item.delta.value - request.short_target_delta.value),
                item.expiry_unix_nanos,
                item.strike,
                str(item.instrument_id),
            ),
        )
        protection = [
            item
            for item in eligible
            if item.expiry_unix_nanos == short.expiry_unix_nanos
            and item.strike < short.strike
            and item.instrument_id != short.instrument_id
        ]
        if not protection:
            raise ValueError(
                "no eligible lower-strike protection leg for selected short Put"
            )
        long = min(
            protection,
            key=lambda item: (
                abs(item.delta.value - request.long_target_delta.value),
                -item.strike.value,
                str(item.instrument_id),
            ),
        )
        return OptionSpreadSelectionResult(short=short, long=long, audit=tuple(audit))

    @staticmethod
    def _rejections(
        candidate: OptionSelectionCandidate,
        request: OptionSpreadSelectionRequest,
    ) -> list[str]:
        reasons: list[str] = []
        if candidate.option_right.upper() not in {"P", "PUT"}:
            reasons.append("not-put")
        if candidate.available_at_unix_nanos > request.decision_time_unix_nanos:
            reasons.append("future-availability")
        if candidate.observed_at_unix_nanos > request.decision_time_unix_nanos:
            reasons.append("future-observation")
        age = request.decision_time_unix_nanos - candidate.observed_at_unix_nanos
        if age > request.maximum_quote_age_nanos:
            reasons.append("stale-quote")
        dte = (
            candidate.expiry_unix_nanos - request.decision_time_unix_nanos
        ) // _DAY_NANOS
        if dte < request.minimum_dte or dte > request.maximum_dte:
            reasons.append("dte-out-of-range")
        if candidate.bid > candidate.ask:
            reasons.append("crossed-quote")
        if not Decimal("-1") <= candidate.delta.value <= Decimal("0"):
            reasons.append("invalid-put-delta")
        return reasons


__all__ = [
    "OptionSelectionAudit",
    "OptionSelectionCandidate",
    "OptionSpreadSelectionApplication",
    "OptionSpreadSelectionRequest",
    "OptionSpreadSelectionResult",
]
