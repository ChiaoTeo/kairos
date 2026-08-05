from datetime import date
from decimal import Decimal

from kairospy.application.usecases.strategy.domain.crash_put import CrashPutCandidate, choose_crash_put


def test_crash_put_selector_prefers_convex_stress_payoff_within_budget() -> None:
    candidates = (
        CrashPutCandidate("near", date(2026, 2, 20), Decimal("570"), Decimal("2"), Decimal("1.9"), Decimal("2.1"), Decimal("600"), Decimal("25")),
        CrashPutCandidate("far-otm", date(2026, 2, 20), Decimal("530"), Decimal("0.8"), Decimal("0.75"), Decimal("0.85"), Decimal("600"), Decimal("30")),
    )
    decision = choose_crash_put(candidates, as_of=date(2026, 1, 1), budget=Decimal("1000"), stress_drop=Decimal("0.20"))

    assert decision is not None
    assert decision.contract == "far-otm"
    assert decision.quantity == 12
    assert decision.max_loss == Decimal("960.0")


def test_crash_put_selector_returns_none_when_no_liquid_expiry_exists() -> None:
    candidate = CrashPutCandidate("wide", date(2026, 1, 10), Decimal("500"), Decimal("1"), Decimal("0.1"), Decimal("2"), Decimal("600"))
    assert choose_crash_put((candidate,), as_of=date(2026, 1, 1), budget=Decimal("1000")) is None
