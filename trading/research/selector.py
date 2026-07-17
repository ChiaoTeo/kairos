from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading.domain.identity import AssetId, InstrumentId
from trading.domain.market_data import OptionChain
from trading.domain.product import ExerciseStyle, ListedOptionSpec, ProductType, SettlementSession, SettlementType

from .spec import ResearchSpec
from trading.reference import ListingDefinition, ListingId, ReferenceCatalog, TradingRules
from trading.reference.factory import publish_instrument
from trading.reference.models import InstrumentDefinition


def select_expirations(
    chain: OptionChain,
    count: int,
    *,
    today: date | None = None,
    minimum_dte_days: int = 0,
    maximum_dte_days: int | None = None,
    target_dte_days: int | None = None,
) -> tuple[date, ...]:
    cutoff = today or date.today()
    eligible = [
        expiry for expiry in sorted(chain.expirations)
        if (expiry - cutoff).days >= minimum_dte_days
        and (maximum_dte_days is None or (expiry - cutoff).days <= maximum_dte_days)
    ]
    if target_dte_days is not None:
        eligible.sort(key=lambda expiry: (abs((expiry - cutoff).days - target_dte_days), expiry))
    return tuple(sorted(eligible[:count]))


def select_strikes(
    strikes: tuple[Decimal, ...],
    spot: Decimal,
    each_side: int,
    *,
    minimum_moneyness: float | None = None,
    maximum_moneyness: float | None = None,
    maximum_strikes: int | None = None,
) -> tuple[Decimal, ...]:
    ordered = sorted(set(strikes))
    if not ordered:
        return ()
    if minimum_moneyness is not None and maximum_moneyness is not None:
        eligible = [item for item in ordered if Decimal(str(minimum_moneyness)) <= item / spot <= Decimal(str(maximum_moneyness))]
        if maximum_strikes is not None and len(eligible) > maximum_strikes:
            indexes = [round(index * (len(eligible) - 1) / (maximum_strikes - 1)) for index in range(maximum_strikes)]
            eligible = [eligible[index] for index in indexes]
        return tuple(eligible)
    nearest_index = min(range(len(ordered)), key=lambda idx: abs(ordered[idx] - spot))
    start = max(0, nearest_index - each_side)
    end = min(len(ordered), nearest_index + each_side + 1)
    return tuple(ordered[start:end])


def select_instruments(catalog: ReferenceCatalog, chain: OptionChain, spot: Decimal, spec: ResearchSpec) -> tuple[InstrumentDefinition, ...]:
    expirations = select_expirations(
        chain, spec.expiry_count, minimum_dte_days=spec.minimum_dte_days,
        maximum_dte_days=spec.maximum_dte_days, target_dte_days=spec.target_dte_days,
    )
    strikes = select_strikes(
        chain.strikes, spot, spec.strikes_each_side,
        minimum_moneyness=spec.minimum_strike_moneyness,
        maximum_moneyness=spec.maximum_strike_moneyness,
        maximum_strikes=spec.maximum_strikes,
    )
    ny = ZoneInfo("America/New_York")
    definitions = []
    for expiry in expirations:
        expiry_at = datetime.combine(expiry, time(16), ny)
        for strike in strikes:
            for right in spec.rights:
                instrument_id = InstrumentId(
                    f"listed-option:spxw:{expiry.isoformat()}:{format(strike, 'f')}:{right.value}"
                )
                existing = tuple(item for item in catalog.instruments.values() if item.instrument_id == instrument_id)
                if existing:
                    definitions.append(existing[-1])
                    continue
                effective_from = datetime(1970, 1, 1, tzinfo=timezone.utc)
                definitions.append(publish_instrument(
                    catalog, instrument_id=instrument_id, instrument_type=ProductType.LISTED_OPTION,
                    display_name="SPXW", contract_spec=ListedOptionSpec(
                        chain.underlying_id, expiry_at, strike, right, ExerciseStyle.EUROPEAN,
                        SettlementType.CASH, SettlementSession.PM, chain.multiplier, expiry_at,
                    ),
                    trading_currency=AssetId(spec.currency), listings=(ListingDefinition(
                        ListingId(f"listing:{chain.venue_id.value}:{instrument_id.value}"), instrument_id,
                        chain.venue_id, instrument_id.value, AssetId(spec.currency),
                        TradingRules(Decimal("0.05"), Decimal("1"), Decimal("1")), effective_from,
                        venue_instrument_id=instrument_id.value,
                    ),), effective_from=effective_from, trading_class=chain.trading_class,
                ))
    return tuple(definitions)
