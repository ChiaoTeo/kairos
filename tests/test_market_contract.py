from collections.abc import Callable
from pathlib import Path
import subprocess
from decimal import Decimal

import pytest

from kairospy.contracts.market import (
    MarketControlClient,
    ExpiryRange,
    MarketCurrentView,
    MarketSubscriptionRequest,
    MarketTarget,
    MarketQuoteCurrent,
    ObservationRequirement,
    OptionFilter,
    OptionRight,
    Options,
    Provider,
    ProviderPreference,
    StrikeRange,
    MarketViewKey,
    MarketViewKind,
)


def test_market_native_subscription_types_own_validation() -> None:
    preference = ProviderPreference.require(Provider.MASSIVE, "binance")
    request = MarketSubscriptionRequest(
        MarketTarget.market("market:binance:spot:BTCUSDT"),
        (ObservationRequirement("quote"), ObservationRequirement.bar("1m")),
        preference,
    )

    assert request.target.kind == "market"
    assert request.target.market_id == "market:binance:spot:BTCUSDT"
    assert request.observation_selectors == ["quote", "bar:1m"]
    assert request.provider_preference == preference
    with pytest.raises(ValueError):
        MarketTarget.options()
    with pytest.raises(ValueError):
        Provider("Not Canonical")


def test_market_control_client_is_native_contract_type(tmp_path: Path) -> None:
    client = MarketControlClient(tmp_path / "market.sock")

    assert type(client).__module__ == "kairospy._native_market_contract"


def test_market_native_option_selection_owns_validation_and_target_mapping() -> None:
    selection = Options("market:exchange:nasdaq:equity:SPY").where(
        expiry=ExpiryRange.next_days(7),
        strike=StrikeRange.around_spot(percent="0.10"),
        right=OptionRight.BOTH,
        limit=40,
    )

    target = selection.to_target(spot="101", now_unix_nanos=1_767_225_600_000_000_000)

    assert type(selection).__module__ == "kairospy._native_market_contract"
    assert target.underlying_market_id == "market:exchange:nasdaq:equity:SPY"
    assert Decimal(target.strike_lower) == Decimal("90.9")
    assert Decimal(target.strike_upper) == Decimal("111.1")
    assert target.expiry_from_unix_nanos == 1_767_225_600_000_000_000
    assert target.expiry_to_unix_nanos == 1_767_916_799_999_999_999
    assert target.option_right == "both"
    assert target.limit == 40

    with pytest.raises(ValueError, match="now_unix_nanos"):
        Options(
            "market:exchange:nasdaq:equity:SPY",
            OptionFilter(expiry=ExpiryRange.next_days(1)),
        ).to_target()


def test_market_view_key_matches_rust_contract_encoding() -> None:
    key = MarketViewKey(
        scope_key="market:binance:spot:BTCUSDT",
        provider="binance",
        kind=MarketViewKind.BAR,
        qualifier="1m",
    )

    assert key.canonical_key() == (
        "scope=market:binance:spot:BTCUSDT;provider=binance;view=bar;qualifier=1m"
    )
    assert str(MarketCurrentView("/tmp/workspace", "workspace").path) == (
        "/tmp/workspace/views/v3/Market/market-main/epoch-1/current.lmdb"
    )


def test_market_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="view identity is incomplete"):
        MarketViewKey("", "binance", MarketViewKind.QUOTE)


@pytest.mark.rust_interop
@pytest.mark.parametrize("with_venue_ids", [False, True])
def test_rust_market_publisher_value_is_readable_by_kairospy(
    tmp_path: Path, rust_contract_example: Callable[[str], Path], with_venue_ids: bool
) -> None:
    subprocess.run(
        [
            rust_contract_example("write_indexed_quote_fixture"),
            str(tmp_path),
            *(["--venue-identities"] if with_venue_ids else []),
        ],
        check=True,
    )
    queries = MarketCurrentView(tmp_path, "workspace", "launch", "instance")
    value = queries.get(
        MarketViewKey("market:fixture", "fixture", MarketViewKind.QUOTE)
    )

    assert value is not None
    assert value.evidence.applied_event_sequence == 41
    assert isinstance(value, MarketQuoteCurrent)
    queries.close()
    # The typed result is Python-owned and does not retain an LMDB transaction.
    assert value.instrument_id == "instrument:fixture"
    assert value.bid_price.mantissa == 12345
    assert value.bid_price.scale == 2
    assert value.bid_venue_id == ("venue:bid" if with_venue_ids else None)
    assert value.ask_venue_id == ("venue:ask" if with_venue_ids else None)
    assert value.bid_venue_code == "19"
    assert value.ask_venue_code == "11"
