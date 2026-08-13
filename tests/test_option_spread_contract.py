from decimal import Decimal

import pytest

from kairospy.strategy import OptionSpreadLegRequest, OptionSpreadRequest


def _leg(leg_id: str, instrument: str, side: str, quantity: str = "1"):
    return OptionSpreadLegRequest(
        leg_id=leg_id,
        instrument_id=instrument,
        side=side,
        quantity=Decimal(quantity),
    )


def test_option_spread_is_one_fixed_risk_package_contract() -> None:
    request = OptionSpreadRequest(
        short_leg=_leg("short", "SPY-P-500", "sell"),
        long_leg=_leg("long", "SPY-P-490", "buy"),
        minimum_net_credit=Decimal("1.20"),
        maximum_loss=Decimal("880"),
        source_snapshot_id="dataset-set:hash",
        source_event_sequence=42,
        source_event_time_unix_nanos=1_700_000_000_000_000_000,
    )

    assert request.short_leg.side == "Sell"
    assert request.long_leg.side == "Buy"
    assert request.completion_policy == "AllOrNothing"
    assert request.failure_policy == "CancelRemaining"


@pytest.mark.parametrize(
    ("short", "long", "message"),
    [
        (_leg("short", "A", "buy"), _leg("long", "B", "buy"), "Sell short"),
        (_leg("short", "A", "sell", "2"), _leg("long", "B", "buy"), "equal"),
        (_leg("short", "A", "sell"), _leg("long", "A", "buy"), "different"),
    ],
)
def test_option_spread_rejects_nonn_package_or_unprotected_shapes(
    short: OptionSpreadLegRequest,
    long: OptionSpreadLegRequest,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OptionSpreadRequest(
            short_leg=short,
            long_leg=long,
            minimum_net_credit=Decimal("1"),
            maximum_loss=Decimal("900"),
        )
