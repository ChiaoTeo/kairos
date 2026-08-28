from decimal import Decimal

import pytest

from kairospy.strategy import (
    OptionSelectionCandidate,
    OptionSpreadSelectionApplication,
    OptionSpreadSelectionRequest,
)


DAY = 86_400_000_000_000
NOW = 1_700_000_000_000_000_000


def _candidate(
    instrument: str,
    *,
    strike: str,
    delta: str,
    expiry_days: int = 35,
    observed: int = NOW - 1_000_000_000,
    available: int = NOW - 500_000_000,
    right: str = "P",
) -> OptionSelectionCandidate:
    return OptionSelectionCandidate(
        instrument_id=instrument,
        reference_snapshot_id="reference-SPY-as-of",
        expiry_unix_nanos=NOW + expiry_days * DAY,
        option_right=right,
        strike=Decimal(strike),
        delta=Decimal(delta),
        bid=Decimal("1.00"),
        ask=Decimal("1.10"),
        observed_at_unix_nanos=observed,
        available_at_unix_nanos=available,
    )


def test_selection_is_point_in_time_deterministic_and_protected() -> None:
    candidates = (
        _candidate("short-tie-z", strike="590", delta="-0.25"),
        _candidate("long", strike="580", delta="-0.10"),
        _candidate("short-tie-a", strike="590", delta="-0.25"),
        _candidate("future", strike="570", delta="-0.08", available=NOW + 1),
        _candidate("call", strike="600", delta="-0.25", right="C"),
    )
    request = OptionSpreadSelectionRequest(
        candidates=candidates, decision_time_unix_nanos=NOW
    )

    first = OptionSpreadSelectionApplication().select(request)
    second = OptionSpreadSelectionApplication().select(request)

    assert first == second
    assert str(first.short.instrument_id) == "short-tie-a"
    assert str(first.long.instrument_id) == "long"
    assert first.long.strike < first.short.strike
    rejected = {
        str(item.instrument_id): item.reasons
        for item in first.audit
        if not item.accepted
    }
    assert rejected == {"call": ("not-put",), "future": ("future-availability",)}


def test_selection_rejects_missing_protection() -> None:
    with pytest.raises(ValueError, match="no eligible lower-strike protection"):
        OptionSpreadSelectionApplication().select(
            OptionSpreadSelectionRequest(
                candidates=(
                    _candidate("short", strike="590", delta="-0.25"),
                    _candidate("higher", strike="600", delta="-0.10"),
                ),
                decision_time_unix_nanos=NOW,
            )
        )
