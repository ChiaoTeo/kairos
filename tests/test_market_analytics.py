from __future__ import annotations

import math

import pytest

from kairospy.application.market import (
    MarketAnalyticalApplication,
    ObservationScope,
    OptionGreeksProjectionRequest,
)


YEAR_NANOS = int(365.25 * 86_400 * 1_000_000_000)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _put_price(spot: float, strike: float, rate: float, sigma: float) -> float:
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2)) / sigma
    d2 = d1 - sigma
    return strike * math.exp(-rate) * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def test_market_derives_reproducible_put_iv_and_greeks_with_lineage() -> None:
    observed = 1_700_000_000_000_000_000
    request = OptionGreeksProjectionRequest(
        scope=ObservationScope.consolidated("instrument:option:SPY:test", "opra"),
        instrument_id="instrument:option:SPY:test",
        option_right="P",
        expiry_unix_nanos=observed + YEAR_NANOS,
        strike=100.0,
        underlying_price=100.0,
        option_price=_put_price(100.0, 100.0, 0.05, 0.2),
        observed_at_unix_nanos=observed,
        available_at_unix_nanos=observed + 1,
        risk_free_rate=0.05,
        price_basis="mid",
        reference_snapshot_id="reference.option-contract/SPY/test",
    )

    first = MarketAnalyticalApplication().option_greeks(request)
    second = MarketAnalyticalApplication().option_greeks(request)

    assert first == second
    assert first.implied_volatility == pytest.approx(0.2, abs=1e-12)
    assert first.delta < 0
    assert first.gamma > 0
    assert first.vega > 0
    payload = first.event["Greeks"]
    assert payload["derivation"] == "black-scholes-european-v1"
    assert payload["price_basis"] == "mid"
    assert payload["available_at_unix_nanos"] == observed + 1
    assert payload["reference_snapshot_id"] == "reference.option-contract/SPY/test"
    assert payload["model_semantics"]["exercise"] == "european-proxy"


def test_market_rejects_future_or_no_arbitrage_invalid_projection_inputs() -> None:
    observed = 1_700_000_000_000_000_000
    with pytest.raises(ValueError, match="available before"):
        OptionGreeksProjectionRequest(
            scope=ObservationScope.market("market"),
            instrument_id="instrument",
            option_right="P",
            expiry_unix_nanos=observed + YEAR_NANOS,
            strike=100,
            underlying_price=100,
            option_price=5,
            observed_at_unix_nanos=observed,
            available_at_unix_nanos=observed - 1,
            risk_free_rate=0.05,
        )
    invalid_price = OptionGreeksProjectionRequest(
        scope=ObservationScope.market("market"),
        instrument_id="instrument",
        option_right="C",
        expiry_unix_nanos=observed + YEAR_NANOS,
        strike=100,
        underlying_price=100,
        option_price=101,
        observed_at_unix_nanos=observed,
        available_at_unix_nanos=observed,
        risk_free_rate=0.05,
    )
    with pytest.raises(ValueError, match="no-arbitrage"):
        MarketAnalyticalApplication().option_greeks(invalid_price)
