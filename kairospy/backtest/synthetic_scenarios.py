from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from kairospy import __version__
from kairospy.trading.identity import AssetId, InstrumentId, VenueId
from kairospy.trading.market_data import Greeks, Quote
from kairospy.trading.product import (
    ExerciseStyle,
    IndexSpec,
    ListedOptionSpec,
    OptionRight,
    ProductType,
    SettlementSession as ProductSettlementSession,
    SettlementType as ProductSettlementType,
)
from kairospy.reference import (
    AssetDefinition,
    AssetType,
    ListingDefinition,
    ListingId,
    ReferenceCatalog,
    TradingRules,
    VenueDefinition,
    VenueType,
)
from kairospy.reference.factory import publish_instrument
from kairospy.capture.snapshot import DataQualityIssue, InstrumentSnapshot

from .feed import InstrumentLifecycleSnapshot, MarketReplayDataset, MarketSnapshot, SettlementType, build_manifest


class SyntheticScenario(StrEnum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    NO_TRADE = "no_trade"
    NEVER_FILLED = "never_filled"
    MISSING_QUOTE = "missing_quote"
    FEE_TURNS_PROFIT_TO_LOSS = "fee_turns_profit_to_loss"
    EXPIRY_ALL_OTM = "expiry_all_otm"
    EXPIRY_SHORT_ITM = "expiry_short_itm"
    EXPIRY_BOTH_ITM = "expiry_both_itm"
    FORCE_CLOSE_FAILURE = "force_close_failure"


def build_synthetic_backtest_dataset(
    scenario: SyntheticScenario = SyntheticScenario.PROFIT_TARGET,
    *,
    split: str = "development",
    start_date: date = date(2025, 1, 6),
) -> MarketReplayDataset:
    tz = ZoneInfo("America/New_York")
    expiry = start_date + timedelta(days=1)
    catalog = ReferenceCatalog()
    effective_from = datetime(1970, 1, 1, tzinfo=timezone.utc)
    underlying = publish_instrument(
        catalog,
        instrument_id=InstrumentId("index:spx"),
        instrument_type=ProductType.INDEX,
        display_name="SPX",
        contract_spec=IndexSpec(AssetId("USD")),
        trading_currency=AssetId("USD"),
        listings=(
            ListingDefinition(
                ListingId("listing:synthetic:spx"),
                InstrumentId("index:spx"),
                VenueId("synthetic"),
                "SPX",
                AssetId("USD"),
                TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")),
                effective_from,
            ),
        ),
        effective_from=effective_from,
        asset_definitions=(
            AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", effective_from, decimals=2),
        ),
        venue_definitions=(
            VenueDefinition(VenueId("synthetic"), VenueType.EXCHANGE, "Synthetic Exchange", "UTC", effective_from),
        ),
    )
    definitions = (
        _put(catalog, expiry, "6050", "101"),
        _put(catalog, expiry, "6000", "102"),
        _put(catalog, expiry, "5950", "103"),
        _put(catalog, expiry, "5900", "104"),
    )
    timestamps = [datetime.combine(start_date, time(15, 30 + minute), tz) for minute in range(4)]
    if scenario in {
        SyntheticScenario.EXPIRY_ALL_OTM,
        SyntheticScenario.EXPIRY_SHORT_ITM,
        SyntheticScenario.EXPIRY_BOTH_ITM,
    }:
        timestamps.append(datetime.combine(expiry, time(16, 1), tz))
    slices = []
    for sequence, timestamp in enumerate(timestamps):
        phase = min(sequence, 3)
        snapshots = []
        for definition in definitions:
            option = definition.contract_spec
            bid, ask, delta = _market_values(option.strike, phase, scenario)
            quote = (
                None
                if scenario in {SyntheticScenario.MISSING_QUOTE, SyntheticScenario.FORCE_CLOSE_FAILURE}
                and phase == 3
                and option.strike == Decimal("6000")
                else Quote(
                    definition.instrument_id,
                    bid,
                    ask,
                    Decimal("20"),
                    Decimal("20"),
                    timestamp,
                )
            )
            greeks = Greeks(
                definition.instrument_id,
                Decimal("0.20"),
                delta,
                Decimal("0.01"),
                Decimal("-1.5"),
                Decimal("1.2"),
                timestamp,
            )
            snapshots.append(
                InstrumentSnapshot(
                    definition.instrument_id,
                    quote,
                    timestamp if quote else None,
                    None,
                    None,
                    greeks,
                    timestamp,
                )
            )
        issues = ()
        if scenario is SyntheticScenario.MISSING_QUOTE and phase == 3:
            issues = (
                DataQualityIssue(
                    "missing_quote",
                    "intentional synthetic missing quote",
                    "error",
                    definitions[1].instrument_id,
                ),
            )
        slices.append(
            MarketSnapshot(
                timestamp,
                tuple(snapshots),
                ((underlying.instrument_id, Decimal("6025")),),
                issues,
                Decimal("0.1"),
                sequence,
            )
        )
    settlement = {
        SyntheticScenario.EXPIRY_ALL_OTM: Decimal("6100"),
        SyntheticScenario.EXPIRY_SHORT_ITM: Decimal("5975"),
        SyntheticScenario.EXPIRY_BOTH_ITM: Decimal("5875"),
    }.get(scenario, Decimal("6025"))
    contracts = tuple(
        InstrumentLifecycleSnapshot(
            definition.instrument_id,
            datetime.combine(expiry, time(16, 0), tz),
            datetime.combine(expiry, time(16, 1), tz),
            SettlementType.PM,
            settlement,
            True,
            "synthetic.fixture",
        )
        for definition in definitions
    )
    slice_tuple = tuple(slices)
    manifest = build_manifest(
        f"synthetic-{scenario.value}-{split}",
        slice_tuple,
        contracts,
        (underlying, *definitions),
        sampling_seconds=60,
        source="synthetic.hand_calculated",
        market_data_type="synthetic",
        code_version=__version__,
        split=split,
        synthetic=True,
        products=catalog.products.values(),
        references=catalog.all_references(),
        settlements=catalog.settlements.values(),
    )
    return MarketReplayDataset(
        manifest,
        slice_tuple,
        contracts,
        (underlying, *definitions),
        catalog.products.values(),
        catalog.all_references(),
        catalog.settlements.values(),
    )


def _put(catalog: ReferenceCatalog, expiry: date, strike: str, external_id: str):
    expiry_at = datetime.combine(expiry, time(16), ZoneInfo("America/New_York"))
    instrument_id = InstrumentId(f"listed-option:spxw:{expiry.isoformat()}:{strike}:put")
    return publish_instrument(
        catalog,
        instrument_id=instrument_id,
        instrument_type=ProductType.LISTED_OPTION,
        display_name=f"SPXW-{expiry.isoformat()}-{strike}-P",
        contract_spec=ListedOptionSpec(
            InstrumentId("index:spx"),
            expiry_at,
            Decimal(strike),
            OptionRight.PUT,
            ExerciseStyle.EUROPEAN,
            ProductSettlementType.CASH,
            ProductSettlementSession.PM,
            Decimal("100"),
            expiry_at,
        ),
        trading_currency=AssetId("USD"),
        listings=(
            ListingDefinition(
                ListingId(f"listing:synthetic:{external_id}"),
                instrument_id,
                VenueId("synthetic"),
                f"SPXW-{expiry.isoformat()}-{strike}-P",
                AssetId("USD"),
                TradingRules(Decimal("0.05"), Decimal("1"), Decimal("1")),
                datetime(1970, 1, 1, tzinfo=timezone.utc),
                venue_instrument_id=external_id,
            ),
        ),
        effective_from=datetime(1970, 1, 1, tzinfo=timezone.utc),
        trading_class="SPXW",
        asset_definitions=(
            AssetDefinition(
                AssetId("USD"),
                AssetType.FIAT,
                "US Dollar",
                datetime(1970, 1, 1, tzinfo=timezone.utc),
                decimals=2,
            ),
        ),
        venue_definitions=(
            VenueDefinition(
                VenueId("synthetic"),
                VenueType.EXCHANGE,
                "Synthetic Exchange",
                "UTC",
                datetime(1970, 1, 1, tzinfo=timezone.utc),
            ),
        ),
    )


def _market_values(strike: Decimal, phase: int, scenario: SyntheticScenario):
    entry = {
        Decimal("6050"): (Decimal("8.0"), Decimal("8.2"), Decimal("-0.40")),
        Decimal("6000"): (Decimal("5.0"), Decimal("5.2"), Decimal("-0.25")),
        Decimal("5950"): (Decimal("2.0"), Decimal("2.2"), Decimal("-0.12")),
        Decimal("5900"): (Decimal("0.8"), Decimal("1.0"), Decimal("-0.05")),
    }
    if phase < 2:
        values = entry[strike]
        if scenario is SyntheticScenario.NO_TRADE:
            no_credit = {
                Decimal("6050"): (Decimal("2.0"), Decimal("2.2")),
                Decimal("6000"): (Decimal("1.0"), Decimal("1.2")),
                Decimal("5950"): (Decimal("2.0"), Decimal("2.2")),
                Decimal("5900"): (Decimal("1.0"), Decimal("1.2")),
            }
            bid, ask = no_credit[strike]
            return bid, ask, values[2]
        if scenario is SyntheticScenario.NEVER_FILLED and phase == 1 and strike == Decimal("6000"):
            return Decimal("1.0"), Decimal("1.2"), values[2]
        return values
    if scenario is SyntheticScenario.STOP_LOSS:
        exit_values = {
            Decimal("6050"): (Decimal("16"), Decimal("16.4")),
            Decimal("6000"): (Decimal("12"), Decimal("12.2")),
            Decimal("5950"): (Decimal("5"), Decimal("5.2")),
            Decimal("5900"): (Decimal("2"), Decimal("2.2")),
        }
    elif scenario is SyntheticScenario.FEE_TURNS_PROFIT_TO_LOSS:
        exit_values = {
            Decimal("6050"): (Decimal("7.9"), Decimal("8.1")),
            Decimal("6000"): (Decimal("4.7"), Decimal("4.9")),
            Decimal("5950"): (Decimal("2.11"), Decimal("2.31")),
            Decimal("5900"): (Decimal("0.8"), Decimal("1.0")),
        }
    else:
        exit_values = {
            Decimal("6050"): (Decimal("3.0"), Decimal("3.2")),
            Decimal("6000"): (Decimal("1.5"), Decimal("1.7")),
            Decimal("5950"): (Decimal("1.0"), Decimal("1.2")),
            Decimal("5900"): (Decimal("0.4"), Decimal("0.6")),
        }
    bid, ask = exit_values[strike]
    return bid, ask, entry[strike][2]


@dataclass(frozen=True, slots=True)
class DatasetReadiness:
    ready: bool
    reasons: tuple[str, ...]
    contract_coverage: Decimal
    quote_coverage: Decimal
    greeks_coverage: Decimal
    stale_rate: Decimal


def assess_dataset(dataset: MarketReplayDataset, minimum_coverage: Decimal = Decimal("0.95")) -> DatasetReadiness:
    manifest = dataset.manifest
    reasons = []
    if manifest.slice_count < 2:
        reasons.append("at least two market slices are required")
    if manifest.contract_coverage < minimum_coverage:
        reasons.append("contract coverage below threshold")
    if manifest.quote_coverage < minimum_coverage:
        reasons.append("quote coverage below threshold")
    if manifest.greeks_coverage < minimum_coverage:
        reasons.append("Greeks coverage below threshold")
    expiring = {ProductType.LISTED_OPTION, ProductType.FUTURE, ProductType.CRYPTO_OPTION}
    if any(item.instrument_type in expiring for item in dataset.definitions) and not dataset.contracts:
        reasons.append("contract metadata is missing for expiring products")
    return DatasetReadiness(
        not reasons,
        tuple(reasons),
        manifest.contract_coverage,
        manifest.quote_coverage,
        manifest.greeks_coverage,
        manifest.stale_rate,
    )


__all__ = [
    "DatasetReadiness",
    "SyntheticScenario",
    "assess_dataset",
    "build_synthetic_backtest_dataset",
]
