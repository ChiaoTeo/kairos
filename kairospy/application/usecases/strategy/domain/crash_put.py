"""Deterministic crash-put selection rules.

The rule intentionally does not know about Massive, persistence, or orders.
Those concerns belong to application/composition; this module only turns a
strategy-owned option-chain snapshot into a bounded-risk proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CrashPutCandidate:
    contract: str
    expiry: date
    strike: Decimal
    premium: Decimal
    bid: Decimal | None
    ask: Decimal | None
    underlying: Decimal
    implied_volatility: Decimal | None = None

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")


@dataclass(frozen=True, slots=True)
class CrashPutDecision:
    contract: str
    quantity: int
    entry_price: Decimal
    max_loss: Decimal
    stress_return: Decimal
    score: Decimal
    reason: str


def choose_crash_put(
    candidates: tuple[CrashPutCandidate, ...],
    *,
    as_of: date,
    budget: Decimal,
    stress_drop: Decimal = Decimal("0.12"),
    max_spread: Decimal = Decimal("0.20"),
    min_days: int = 21,
    max_days: int = 75,
) -> CrashPutDecision | None:
    """Choose a cheap far-OTM put for a large-drop thesis.

    ``stress_return`` is the expiry payoff under the configured crash,
    divided by entry cost.  It is not a forecast or an expected value; it is
    a transparent scenario score.  The strategy therefore cannot claim that
    a contract is objectively "best" without an explicit scenario/budget.
    """
    if budget <= 0 or not (Decimal("0") < stress_drop < Decimal("1")):
        raise ValueError("budget must be positive and stress_drop must be between 0 and 1")
    if min_days < 1 or max_days < min_days:
        raise ValueError("invalid expiry window")

    ranked: list[tuple[Decimal, CrashPutCandidate, Decimal]] = []
    stressed_underlying = None
    for candidate in candidates:
        days = (candidate.expiry - as_of).days
        mid = candidate.midpoint
        if days < min_days or days > max_days or mid is None:
            continue
        if candidate.underlying <= 0 or candidate.strike <= 0 or mid <= 0:
            continue
        spread = (candidate.ask - candidate.bid) / mid if candidate.bid is not None and candidate.ask is not None else Decimal("1")
        if spread > max_spread:
            continue
        stressed_underlying = candidate.underlying * (Decimal("1") - stress_drop)
        payoff = max(candidate.strike - stressed_underlying, Decimal("0"))
        stress_return = payoff / mid
        # Prefer the convex payoff, but penalize excessive time and IV.
        iv_penalty = (candidate.implied_volatility or Decimal("0")) / Decimal("100")
        score = stress_return - Decimal(days) / Decimal("1000") - iv_penalty
        ranked.append((score, candidate, mid))

    if not ranked:
        return None
    score, candidate, entry = max(ranked, key=lambda item: item[0])
    quantity = int(budget // (entry * Decimal("100")))
    if quantity < 1:
        return None
    stressed = candidate.underlying * (Decimal("1") - stress_drop)
    payoff = max(candidate.strike - stressed, Decimal("0"))
    return CrashPutDecision(
        contract=candidate.contract,
        quantity=quantity,
        entry_price=entry,
        max_loss=entry * Decimal("100") * quantity,
        stress_return=payoff / entry,
        score=score,
        reason=f"{stress_drop:.0%} stress payoff with {candidate.expiry.isoformat()} expiry",
    )


__all__ = ["CrashPutCandidate", "CrashPutDecision", "choose_crash_put"]
