from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from kairos.domain.identity import InstrumentId
from kairos.domain.market_data import OptionChain
from kairos.reference.contracts import InstrumentDefinition
from kairos.domain.market_data import Greeks, Quote, Trade
from kairos.domain.market_state import MarketState

from .spec import OptionChainCaptureSpec
if TYPE_CHECKING:
    from kairos.reference.catalog import ReferenceCatalog

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str = "warning"
    instrument_id: InstrumentId | None = None


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    instrument_id: InstrumentId
    quote: Quote | None
    quote_time: datetime | None
    trade: Trade | None
    trade_time: datetime | None
    greeks: Greeks | None
    greeks_time: datetime | None


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    schema_version: int
    run_id: UUID
    created_at: datetime
    spec: OptionChainCaptureSpec
    underlying_id: InstrumentId
    underlying_price: Decimal
    underlying_price_time: datetime
    option_chain: OptionChain
    definitions: tuple[InstrumentDefinition, ...]
    instruments: tuple[InstrumentSnapshot, ...]
    sources: tuple[str, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    snapshot_span_seconds: float
    code_version: str


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotEvidence:
    as_of: datetime
    instrument_ids: tuple[InstrumentId, ...]
    product_ids: tuple[str, ...]
    listing_ids: tuple[str, ...]
    mapping_keys: tuple[str, ...]
    reference_count: int
    content_hash: str


def build_reference_evidence(catalog: ReferenceCatalog, instrument_ids: tuple[InstrumentId, ...], as_of: datetime) -> ReferenceSnapshotEvidence:
    from kairos.storage.codec import to_primitive
    if as_of.tzinfo is None:
        raise ValueError("reference evidence time must be timezone-aware")
    unique = tuple(sorted(set(instrument_ids), key=lambda item: item.value))
    definitions = tuple(catalog.instruments.get(item, as_of) for item in unique)
    product_ids = tuple(sorted({item.product_id.value for item in definitions}))
    listings = tuple(sorted(
        (listing for instrument_id in unique for listing in catalog.active_listings(instrument_id, as_of)),
        key=lambda item: item.listing_id.value,
    ))
    mappings = tuple(sorted(
        (item for item in catalog.mappings() if item.active_at(as_of) and item.target_id in {value.value for value in unique}),
        key=lambda item: (item.provider_id.value, item.namespace, item.external_id),
    ))
    references = tuple(sorted(
        (item for item in catalog.all_references() if item.active_at(as_of) and item.source_instrument_id in unique),
        key=lambda item: (item.source_instrument_id.value, item.role.value, str(item.target)),
    ))
    material = {
        "as_of": as_of,
        "definitions": definitions,
        "products": tuple(catalog.products.get(item.product_id, as_of) for item in definitions),
        "listings": listings,
        "mappings": mappings,
        "references": references,
        "settlements": tuple(
            catalog.settlements.get(item.settlement_terms_id, as_of)
            for item in definitions if item.settlement_terms_id is not None
        ),
    }
    encoded = json.dumps(to_primitive(material), sort_keys=True, separators=(",", ":"))
    return ReferenceSnapshotEvidence(
        as_of, unique, product_ids, tuple(item.listing_id.value for item in listings),
        tuple(f"{item.provider_id.value}:{item.namespace}:{item.external_id}" for item in mappings),
        len(references), sha256(encoded.encode()).hexdigest(),
    )


def build_snapshot(
    *,
    run_id: UUID,
    spec: OptionChainCaptureSpec,
    underlying: InstrumentDefinition,
    chain: OptionChain,
    selected: tuple[InstrumentDefinition, ...],
    catalog: ReferenceCatalog,
    state: MarketState,
    now: datetime | None = None,
    code_version: str = "0.1.0",
) -> ResearchSnapshot:
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        raise ValueError("snapshot time must be timezone-aware")
    price_entry = state.underlying_prices.get(underlying.instrument_id)
    if not price_entry or price_entry[0] <= 0:
        raise ValueError("a positive underlying price is required")
    price, price_time = price_entry
    issues: list[DataQualityIssue] = []
    if price_time.tzinfo is None:
        raise ValueError("underlying price timestamp must be timezone-aware")
    if created_at - price_time > timedelta(seconds=spec.max_quote_age_seconds):
        issues.append(DataQualityIssue("stale_underlying", "underlying price is stale", "error", underlying.instrument_id))
    snapshots: list[InstrumentSnapshot] = []
    times = [price_time]
    for definition in selected:
        instrument_id = definition.instrument_id
        listings = catalog.active_listings(instrument_id, created_at)
        if not listings or not listings[0].venue_instrument_id:
            issues.append(DataQualityIssue("unqualified_contract", "contract has no broker identifier", "error", instrument_id))
        item = state.instruments.get(instrument_id)
        if item is None:
            snapshots.append(InstrumentSnapshot(instrument_id, None, None, None, None, None, None))
            issues.append(DataQualityIssue("missing_market_data", "no market data received", "error", instrument_id))
            continue
        quote = item.quote
        if quote is None:
            issues.append(DataQualityIssue("missing_quote", "quote is missing", "error", instrument_id))
        else:
            if quote.bid is not None and quote.bid < 0 or quote.ask is not None and quote.ask < 0:
                issues.append(DataQualityIssue("negative_quote", "bid or ask is negative", "error", instrument_id))
            if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
                issues.append(DataQualityIssue("crossed_quote", "bid exceeds ask", "error", instrument_id))
        if item.greeks is None:
            issues.append(DataQualityIssue("missing_greeks", "model Greeks are missing", "warning", instrument_id))
        for label, timestamp in (("quote", item.quote_time), ("trade", item.trade_time), ("greeks", item.greeks_time)):
            if timestamp is not None:
                if timestamp.tzinfo is None:
                    raise ValueError(f"{label} timestamp must be timezone-aware")
                times.append(timestamp)
                if created_at - timestamp > timedelta(seconds=spec.max_quote_age_seconds):
                    issues.append(DataQualityIssue("stale_data", f"{label} is stale", "warning", instrument_id))
        snapshots.append(
            InstrumentSnapshot(
                instrument_id, item.quote, item.quote_time, item.trade, item.trade_time, item.greeks, item.greeks_time
            )
        )
    span = (max(times) - min(times)).total_seconds() if times else 0.0
    sources = tuple(sorted({event.source for event in state.events}))
    return ResearchSnapshot(
        SCHEMA_VERSION,
        run_id,
        created_at,
        spec,
        underlying.instrument_id,
        price,
        price_time,
        chain,
        (underlying, *selected),
        tuple(snapshots),
        sources,
        tuple(issues),
        span,
        code_version,
    )
