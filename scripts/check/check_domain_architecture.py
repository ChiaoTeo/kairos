#!/usr/bin/env python3
"""Cheap, deterministic architecture checks for the Rust workspace.

Contract and generated models are intentionally excluded: they are wire
representations.  This check protects the business-domain boundaries while
the stronger primitive-field migration proceeds module by module.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
BUSINESS = ROOT / "crates" / "business"

TYPED_FIELDS = {
    ROOT / "crates" / "business" / "reference" / "service" / "src" / "domain" / "entities.rs": [
        r"pub asset_id: AssetId",
        r"pub symbol: Symbol",
        r"pub source_symbol: Symbol",
        r"pub exchange_symbol: Symbol",
        r"pub issuer_id: Option<IssuerId>",
        r"pub provider_symbol: ProviderSymbol",
        r"pub effective_from_unix_nanos: UnixNanos",
        r"pub event_time_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "kairos-integration" / "src" / "application" / "capabilities" / "execution.rs": [
        r"pub symbol: Option<Symbol>",
        r"pub order_id: Option<OrderId>",
        r"pub since_unix_millis: Option<UnixNanos>",
        r"pub occurred_at_unix_millis: Option<UnixNanos>",
        r"pub order_id: OrderId",
        r"pub symbol: Symbol",
        r"pub execution_id: Option<FillId>",
        r"pub occurred_at_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "kairos-integration" / "src" / "application" / "capabilities" / "market.rs": [
        r"pub symbol: Symbol",
        r"pub start_time_unix_nanos: UnixNanos",
        r"pub end_time_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "kairos-integration" / "src" / "application" / "capabilities" / "account.rs": [
        r"pub account_id: AccountId",
        r"pub segment_key: SegmentKey",
        r"pub market_id: MarketId",
        r"pub source_symbol: Symbol",
        r"pub fee_currency: Option<Currency>",
        r"pub observed_at_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "business" / "reference" / "service" / "src" / "application" / "queries.rs": [
        r"pub as_of_unix_nanos: Option<UnixNanos>",
        r"pub sequence_from: Option<Sequence>",
        r"pub event_time_from_unix_nanos: Option<UnixNanos>",
        r"pub market_id: Option<MarketId>",
        r"pub source_symbol: Option<Symbol>",
    ],
    ROOT / "crates" / "business" / "market" / "service" / "src" / "domain" / "reference.rs": [
        r"pub generation: Generation",
        r"pub event_sequence: Sequence",
    ],
    ROOT / "crates" / "business" / "market" / "service" / "src" / "domain" / "snapshot.rs": [
        r"pub generation: Generation",
        r"pub event_sequence: Sequence",
    ],
    ROOT / "crates" / "business" / "execution" / "service" / "src" / "application" / "mod.rs": [
        r"pub order_id: kairos_domain_types::OrderId",
        r"pub symbol: kairos_domain_types::Symbol",
        r"pub fill_quantity: Option<kairos_domain_types::Quantity>",
        r"pub fill_price: Option<kairos_domain_types::Price>",
        r"pub occurred_at_unix_nanos: kairos_domain_types::UnixNanos",
    ],
    ROOT / "crates" / "business" / "execution" / "service" / "src" / "application" / "service.rs": [
        r"pub instrument_id: InstrumentId",
        r"pub market_id: Option<MarketId>",
        r"pub bid_price: Option<Price>",
        r"pub ask_price: Option<Price>",
        r"pub observed_at_unix_nanos: UnixNanos",
        r"pub fill_id: FillId",
        r"pub order_id: OrderId",
        r"pub quantity: Quantity",
        r"pub price: Price",
        r"pub fee: Money",
        r"pub occurred_at_unix_nanos: Option<UnixNanos>",
        r"pub intent_id: IntentId",
        r"pub bid_price: Price",
        r"pub ask_price: Price",
        r"pub quote_observed_at: UnixNanos",
        r"pub order_id: Option<OrderId>",
        r"pub remote_order_id: Option<kairos_domain_types::RemoteOrderId>",
        r"pub since_unix_nanos: Option<UnixNanos>",
        r"pub until_unix_nanos: Option<UnixNanos>",
        r"pub sequence: Sequence",
        r"pub remote_order_id: kairos_domain_types::RemoteOrderId",
        r"pub symbol: Symbol",
        r"pub execution_id: Option<FillId>",
        r"pub fill_quantity: Option<Quantity>",
        r"pub fill_price: Option<Price>",
        r"pub fee_currency: Option<Currency>",
        r"pub fee_amount: Option<Money>",
        r"pub order_id: OrderId",
        r"pub client_order_id: Option<ClientOrderId>",
        r"pub symbol: Symbol",
        r"pub quantity: Quantity",
        r"pub filled_quantity: Quantity",
        r"pub average_fill_price: Option<Price>",
        r"pub occurred_at_unix_nanos: Option<UnixNanos>",
        r"pub first_seen_at_unix_nanos: UnixNanos",
        r"pub last_seen_at_unix_nanos: UnixNanos",
        r"pub generation: Generation",
        r"pub event_sequence: Sequence",
        r"pub actor_id: ActorId",
        r"pub exchange_event_watermark_unix_nanos: UnixNanos",
        r"pub intent_id: IntentId",
        r"pub target_quantity: Quantity",
        r"pub account_ids: Vec<AccountId>",
        r"pub source_event_sequence: Option<Sequence>",
        r"pub deadline_unix_nanos: Option<UnixNanos>",
        r"pub leg_id: LegId",
        r"pub account_id: AccountId",
        r"pub segment_key: SegmentKey",
        r"pub instrument_id: InstrumentId",
        r"pub market_id: Option<MarketId>",
        r"pub quantity: Quantity",
        r"pub limit_price: Option<Price>",
        r"pub occurred_at_unix_nanos: UnixNanos",
        r"pub intent_id: IntentId",
        r"pub order_id: OrderId",
        r"pub intent_id: Option<IntentId>",
        r"pub plan_id: Option<PlanId>",
        r"pub leg_id: Option<LegId>",
        r"pub remote_order_id: Option<RemoteOrderId>",
        r"pub fill_id: Option<FillId>",
        r"pub order_ids: Vec<OrderId>",
        r"pub updated_at_unix_nanos: UnixNanos",
        r"pub last_quote_refresh_unix_nanos: Option<UnixNanos>",
        r"pub pending_order_due_unix_nanos: BTreeMap<OrderId, UnixNanos>",
        r"pub leader_leg_id: LegId",
        r"pub hedge_leg_id: LegId",
    ],
    ROOT / "crates" / "business" / "execution" / "service" / "src" / "services" / "simulator.rs": [
        r"pub order_id: OrderId",
        r"pub instrument_id: InstrumentId",
        r"pub quantity: Quantity",
        r"pub limit_price: Option<Price>",
        r"pub submitted_at_unix_nanos: UnixNanos",
        r"pub filled_quantity: Quantity",
        r"pub remaining_quantity: Quantity",
        r"pub updated_at_unix_nanos: UnixNanos",
        r"pub fill_id: FillId",
        r"pub fee: Money",
        r"pub occurred_at_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "business" / "execution" / "service" / "src" / "application" / "backtest.rs": [
        r"pub observed_at_unix_nanos: UnixNanos",
        r"pub equity: Money",
        r"pub instrument_id: InstrumentId",
        r"pub quantity: Quantity",
        r"pub price: Price",
        r"pub fee: Money",
        r"pub occurred_at_unix_nanos: UnixNanos",
        r"pub initial_equity: Money",
        r"pub risk_free_rate: Rate",
    ],
    ROOT / "crates" / "business" / "account" / "service" / "src" / "services" / "persistence.rs": [
        r"pub generation: Generation",
        r"pub event_sequence: Sequence",
    ],
    ROOT / "crates" / "business" / "account" / "service" / "src" / "domain" / "market_profile.rs": [
        r"pub account_id: AccountId",
        r"pub market_id: MarketId",
        r"pub fee_currency: Option<Currency>",
        r"pub observed_at_unix_nanos: UnixNanos",
    ],
    ROOT / "crates" / "business" / "account" / "service" / "src" / "application" / "result.rs": [
        r"pub account_id: AccountId",
        r"pub settlement_assets: Vec<Currency>",
        r"pub currency: Option<Currency>",
    ],
}

# Wire models may use primitives, but core domain field names must not regress.
FORBIDDEN_DOMAIN_PRIMITIVES = re.compile(
    r"\bpub\s+(?:account_id|order_id|client_order_id|currency|symbol|"
    r"exchange_id|market_id|instrument_id|quantity|price|sequence|generation|"
    r"event_sequence|[A-Za-z0-9_]+_unix_nanos)\s*:\s*"
    r"(?:Option<|Vec<)?(?:String|u64|i64)"
)


def rust_sources() -> list[Path]:
    paths = list(BUSINESS.glob("*/service/src/**/*.rs"))
    paths += list((ROOT / "crates" / "kairos-integration" / "src" / "domain").glob("**/*.rs"))
    return paths


def main() -> int:
    failures: list[str] = []
    canonical = {"OrderSide": [], "OrderStatus": []}

    for path in rust_sources():
        text = path.read_text()
        # Provider-native `source_venue` is an allowed external fact until
        # Reference maps it to canonical Exchange identity. Only canonical
        # business identifiers/types using the retired term are forbidden.
        legacy_exchange_identifier = r"\b(?:Venue|venue_id)\b"
        if re.search(legacy_exchange_identifier, text):
            failures.append(f"forbidden legacy exchange terminology in Rust source: {path}")
        production = "#[cfg(test)]" not in text or path.parent.name != "tests"
        if production:
            for name in canonical:
                if re.search(rf"\bpub\s+enum\s+{name}\b", text):
                    canonical[name].append(path)
        is_test = path.name.endswith("_tests.rs") or path.name == "tests.rs" or "tests" in path.parts
        if re.search(r"use\s+kairos_[a-z0-9_]+::services", text) and not is_test:
            failures.append(f"cross-module private services import in production file: {path}")
        if "domain" in path.parts:
            for match in FORBIDDEN_DOMAIN_PRIMITIVES.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"core domain field regressed to a primitive: {path}:{line}")

    for name, paths in canonical.items():
        if len(paths) > 1:
            failures.append(f"duplicate canonical {name}: {', '.join(map(str, paths))}")

    for path, patterns in TYPED_FIELDS.items():
        text = path.read_text()
        for pattern in patterns:
            if not re.search(pattern, text):
                failures.append(f"required typed field is missing: {path} / {pattern}")

    if failures:
        print("domain architecture checks failed:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("domain architecture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
