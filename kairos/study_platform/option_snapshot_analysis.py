from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from kairos.domain.product import ListedOptionSpec, OptionRight
from kairos.reference.access import contract_spec

from .snapshot import OptionCaptureSnapshot

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OptionSnapshotMetricRow:
    symbol: str
    expiry: str
    strike: Decimal
    right: str
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    mid: Decimal | None
    spread: Decimal | None
    spread_pct: Decimal | None
    moneyness: Decimal
    minutes_to_expiry: Decimal
    years_to_expiry: Decimal
    implied_vol: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    theta_per_premium: Decimal | None
    vega_per_premium: Decimal | None
    theta_per_spread: Decimal | None
    vega_per_spread: Decimal | None
    paired_mid: Decimal | None
    paired_iv: Decimal | None
    iv_minus_pair_iv: Decimal | None
    quote_present: bool
    greeks_present: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class OptionSnapshotAnalysis:
    run_id: str
    generated_at: datetime
    snapshot_span_seconds: float
    completeness_rate: Decimal
    stale_rate: Decimal
    rows: tuple[OptionSnapshotMetricRow, ...]
    iv_smile: tuple["IvSmilePoint", ...]
    put_call_pairs: tuple["PutCallPair", ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(field.name for field in fields(OptionSnapshotMetricRow))


@dataclass(frozen=True, slots=True)
class IvSmilePoint:
    expiry: str
    strike: Decimal
    right: str
    implied_vol: Decimal


@dataclass(frozen=True, slots=True)
class PutCallPair:
    expiry: str
    strike: Decimal
    call_mid: Decimal | None
    put_mid: Decimal | None
    call_iv: Decimal | None
    put_iv: Decimal | None
    iv_difference: Decimal | None


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, ZERO):
        return None
    return numerator / denominator


def analyze_option_snapshot(snapshot: OptionCaptureSnapshot) -> OptionSnapshotAnalysis:
    rows: list[OptionSnapshotMetricRow] = []
    stale_keys = {
        issue.instrument_id.value
        for issue in snapshot.quality_issues
        if issue.code == "stale_data" and issue.instrument_id is not None
    }
    definitions = {item.instrument_id: item for item in snapshot.definitions}
    for item in snapshot.instruments:
        definition, quote, greeks = definitions[item.instrument_id], item.quote, item.greeks
        if not isinstance(contract_spec(definition), ListedOptionSpec):
            continue
        option = contract_spec(definition)
        bid, ask = (quote.bid, quote.ask) if quote else (None, None)
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        spread = ask - bid if bid is not None and ask is not None else None
        expiry_at = option.expiry
        seconds = max(Decimal("0"), Decimal(str((expiry_at - snapshot.created_at).total_seconds())))
        minutes = seconds / Decimal("60")
        years = seconds / Decimal("31557600")
        theta = greeks.theta if greeks else None
        vega = greeks.vega if greeks else None
        rows.append(
            OptionSnapshotMetricRow(
                getattr(definition, "display_name", None) or getattr(definition, "symbol", definition.instrument_id.value),
                option.expiry.date().isoformat(),
                option.strike,
                "C" if option.right is OptionRight.CALL else "P",
                bid,
                ask,
                quote.bid_size if quote else None,
                quote.ask_size if quote else None,
                mid,
                spread,
                _ratio(spread, mid),
                option.strike / snapshot.underlying_price - 1,
                minutes,
                years,
                greeks.implied_volatility if greeks else None,
                greeks.delta if greeks else None,
                greeks.gamma if greeks else None,
                theta,
                vega,
                _ratio(theta, mid),
                _ratio(vega, mid),
                _ratio(theta, spread),
                _ratio(vega, spread),
                None,
                None,
                None,
                quote is not None,
                greeks is not None,
                item.instrument_id.value in stale_keys,
            )
        )
    rows.sort(key=lambda row: (row.expiry, row.strike, row.right))
    total = Decimal(len(rows))
    complete = sum(1 for row in rows if row.quote_present and row.greeks_present)
    stale = sum(1 for row in rows if row.stale)
    smiles = tuple(
        IvSmilePoint(row.expiry, row.strike, row.right, row.implied_vol)
        for row in rows
        if row.implied_vol is not None
    )
    grouped: dict[tuple[str, Decimal], dict[str, OptionSnapshotMetricRow]] = {}
    for row in rows:
        grouped.setdefault((row.expiry, row.strike), {})[row.right] = row
    pairs = []
    for (expiry, strike), rights in sorted(grouped.items()):
        call, put = rights.get("C"), rights.get("P")
        if call is None or put is None:
            continue
        difference = call.implied_vol - put.implied_vol if call.implied_vol is not None and put.implied_vol is not None else None
        pairs.append(PutCallPair(expiry, strike, call.mid, put.mid, call.implied_vol, put.implied_vol, difference))
    paired_rows = []
    for row in rows:
        pair = grouped[(row.expiry, row.strike)].get("P" if row.right == "C" else "C")
        difference = row.implied_vol - pair.implied_vol if pair and row.implied_vol is not None and pair.implied_vol is not None else None
        paired_rows.append(
            replace(
                row,
                paired_mid=pair.mid if pair else None,
                paired_iv=pair.implied_vol if pair else None,
                iv_minus_pair_iv=difference,
            )
        )
    return OptionSnapshotAnalysis(
        str(snapshot.run_id),
        datetime.now(timezone.utc),
        snapshot.snapshot_span_seconds,
        Decimal(complete) / total if total else ZERO,
        Decimal(stale) / total if total else ZERO,
        tuple(paired_rows),
        smiles,
        tuple(pairs),
    )
