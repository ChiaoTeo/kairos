from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from trading import __version__
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.market_data import Greeks, Quote
from trading.domain.product import ExerciseStyle, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession as ProductSettlementSession, SettlementType as ProductSettlementType
from trading.research.snapshot import DataQualityIssue, InstrumentSnapshot

from .feed import (
    ContractMetadata,
    HistoricalDataset,
    MarketSlice,
    SettlementType,
    build_manifest,
)


class MockScenario(StrEnum):
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


def make_mock_dataset(
    scenario: MockScenario = MockScenario.PROFIT_TARGET,
    *,
    split: str = "development",
    start_date: date = date(2025, 1, 6),
) -> HistoricalDataset:
    tz = ZoneInfo("America/New_York")
    expiry = start_date + timedelta(days=1)
    underlying = InstrumentDefinition(
        InstrumentId("index:spx"), ProductType.INDEX, "SPX", None, AssetId("USD"), IndexSpec(AssetId("USD")),
        (VenueListing(VenueId("mock"), "SPX", "SPX", Decimal("0.01"), Decimal("1"), Decimal("1")),),
        datetime(1970, 1, 1, tzinfo=timezone.utc),
    )
    definitions = (
        _put(expiry, "6050", "101"),
        _put(expiry, "6000", "102"),
        _put(expiry, "5950", "103"),
        _put(expiry, "5900", "104"),
    )
    timestamps = [datetime.combine(start_date, time(15, 30 + minute), tz) for minute in range(4)]
    if scenario in {MockScenario.EXPIRY_ALL_OTM, MockScenario.EXPIRY_SHORT_ITM, MockScenario.EXPIRY_BOTH_ITM}:
        timestamps.append(datetime.combine(expiry, time(16, 1), tz))
    slices = []
    for sequence, timestamp in enumerate(timestamps):
        phase = min(sequence, 3)
        snapshots = []
        for definition in definitions:
            option = definition.product_spec
            bid, ask, delta = _market_values(option.strike, phase, scenario)
            quote = None if scenario in {MockScenario.MISSING_QUOTE, MockScenario.FORCE_CLOSE_FAILURE} and phase == 3 and option.strike == Decimal("6000") else Quote(definition.instrument_id, bid, ask, Decimal("20"), Decimal("20"), timestamp)
            greeks = Greeks(definition.instrument_id, Decimal("0.20"), delta, Decimal("0.01"), Decimal("-1.5"), Decimal("1.2"), timestamp)
            snapshots.append(InstrumentSnapshot(definition.instrument_id, quote, timestamp if quote else None, None, None, greeks, timestamp))
        issues = ()
        if scenario is MockScenario.MISSING_QUOTE and phase == 3:
            issues = (DataQualityIssue("missing_quote", "intentional mock missing quote", "error", definitions[1].instrument_id),)
        slices.append(MarketSlice(timestamp, tuple(snapshots), ((underlying.instrument_id, Decimal("6025")),), issues, Decimal("0.1"), sequence))
    settlement = {
        MockScenario.EXPIRY_ALL_OTM: Decimal("6100"),
        MockScenario.EXPIRY_SHORT_ITM: Decimal("5975"),
        MockScenario.EXPIRY_BOTH_ITM: Decimal("5875"),
    }.get(scenario, Decimal("6025"))
    contracts = tuple(
        ContractMetadata(
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
        f"mock-{scenario.value}-{split}", slice_tuple, contracts, (underlying, *definitions), sampling_seconds=60,
        source="synthetic.hand_calculated", market_data_type="mock", code_version=__version__, split=split, synthetic=True,
    )
    return HistoricalDataset(manifest, slice_tuple, contracts, (underlying, *definitions))


def _put(expiry: date, strike: str, external_id: str) -> InstrumentDefinition:
    expiry_at = datetime.combine(expiry, time(16), ZoneInfo("America/New_York"))
    instrument_id = InstrumentId(f"listed-option:spxw:{expiry.isoformat()}:{strike}:put")
    return InstrumentDefinition(
        instrument_id, ProductType.LISTED_OPTION, "SPXW", None, AssetId("USD"),
        ListedOptionSpec(
            InstrumentId("index:spx"), expiry_at, Decimal(strike), OptionRight.PUT,
            ExerciseStyle.EUROPEAN, ProductSettlementType.CASH, ProductSettlementSession.PM,
            Decimal("100"), expiry_at,
        ),
        (VenueListing(VenueId("mock"), external_id, f"SPXW-{expiry.isoformat()}-{strike}-P", Decimal("0.05"), Decimal("1"), Decimal("1")),),
        datetime(1970, 1, 1, tzinfo=timezone.utc),
    )


def _market_values(strike: Decimal, phase: int, scenario: MockScenario):
    entry = {
        Decimal("6050"): (Decimal("8.0"), Decimal("8.2"), Decimal("-0.40")),
        Decimal("6000"): (Decimal("5.0"), Decimal("5.2"), Decimal("-0.25")),
        Decimal("5950"): (Decimal("2.0"), Decimal("2.2"), Decimal("-0.12")),
        Decimal("5900"): (Decimal("0.8"), Decimal("1.0"), Decimal("-0.05")),
    }
    if phase < 2:
        values = entry[strike]
        if scenario is MockScenario.NO_TRADE:
            no_credit = {
                Decimal("6050"): (Decimal("2.0"), Decimal("2.2")),
                Decimal("6000"): (Decimal("1.0"), Decimal("1.2")),
                Decimal("5950"): (Decimal("2.0"), Decimal("2.2")),
                Decimal("5900"): (Decimal("1.0"), Decimal("1.2")),
            }
            bid, ask = no_credit[strike]
            return bid, ask, values[2]
        if scenario is MockScenario.NEVER_FILLED and phase == 1 and strike == Decimal("6000"):
            return Decimal("1.0"), Decimal("1.2"), values[2]
        return values
    if scenario is MockScenario.STOP_LOSS:
        exit_values = {
            Decimal("6050"): (Decimal("16"), Decimal("16.4")),
            Decimal("6000"): (Decimal("12"), Decimal("12.2")),
            Decimal("5950"): (Decimal("5"), Decimal("5.2")),
            Decimal("5900"): (Decimal("2"), Decimal("2.2")),
        }
    elif scenario is MockScenario.FEE_TURNS_PROFIT_TO_LOSS:
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


def assess_dataset(dataset: HistoricalDataset, minimum_coverage: Decimal = Decimal("0.95")) -> DatasetReadiness:
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
    if any(item.product_type in expiring for item in dataset.definitions) and not dataset.contracts:
        reasons.append("contract metadata is missing for expiring products")
    return DatasetReadiness(not reasons, tuple(reasons), manifest.contract_coverage, manifest.quote_coverage, manifest.greeks_coverage, manifest.stale_rate)
