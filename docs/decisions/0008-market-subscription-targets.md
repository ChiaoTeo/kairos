# Decision 0008: Market Subscription Target API

- Status: Superseded by [Decision 0012](0012-market-provider-routes.md)
- Date: 2026-08-21
- Scope: Python Strategy Market API, Market contract, Market runtime subscription resolution

## Context

Strategy Market subscriptions previously exposed too many internal routing terms at the user API:

```python
ctx.market.subscribe(
    symbol="AAPL",
    asset="equity",
    data=["quote", "bar:1m"],
    scope="consolidated",
    sources="all",
)
```

`symbol` is understandable to a strategy author, but `asset`, `scope`, provider product, network ID,
and provider symbol are not the user's primary intent. They are Reference and Market resolution facts.
Option subscriptions made the problem worse: strategies had to discover an underlying, manually filter
contracts, reconstruct provider symbols such as OPRA/Massive tickers, and subscribe each selected
contract through provider-specific arguments.

The product direction is to make Market subscriptions read as one action with three separate meanings:

- target: what business market or selection to subscribe to;
- data: which observations are required;
- sources: which provider routes may satisfy the subscription.

Options should not become a separate top-level verb such as `subscribe_option_chain`. The action is still
`subscribe`; the difference is the target.

## Decision

### One subscription action

Strategy code uses one Market subscription action:

```python
ctx.market.subscribe(target, data=[...], participants=...)
```

The target owns "what", `data` owns "which observation types", and `sources` owns provider routing.
`scope`, `asset`, provider product, provider symbol, and provider network do not become normal
strategy-facing subscription parameters.

### Static market target

For one explicit market, the target is a `MarketId` or a Reference `Market`:

```python
ctx.market.subscribe(
    MarketId("market:exchange:nasdaq:equity:AAPL"),
    data=[MarketData.QUOTE, MarketData.bar(Timeframe.MIN_1)],
    participants=ParticipantSet.only(Participant.MASSIVE, Participant.BINANCE),
)
```

The Python SDK converts typed selectors and source constants into contract wire values. The Market
contract accepts multiple source IDs, and the Market runtime expands one market identity into one
source-constrained subscription member per selected source.

### Option selection target

An option window is modeled as a target, not as a second subscribe function:

```python
ctx.market.subscribe(
    Options(
        underlying=spy,
        filter=OptionFilter(
            expiry=ExpiryRange.next_days(7),
            strike=StrikeRange.around_spot(percent=Decimal("0.02")),
            right=OptionRight.BOTH,
            limit=40,
        ),
    ),
    data=[MarketData.QUOTE, MarketData.GREEKS],
    participants=ParticipantSet.only(Participant.MASSIVE),
)
```

`Options` represents a dynamic selection of option markets for one underlying. It is not a provider
symbol and not a raw chain feed. Internally, Market may continue to use "chain", "universe", or
"selection" as implementation terms, but those terms are not the strategy API.

The target should support underlying identities in this order:

- `MarketId` or Reference `Market` for the underlying market;
- `InstrumentId` or Reference `Instrument` when no canonical market is needed;
- a symbol only in convenience layers that immediately resolve through Reference.

### Option filter

The first product-level option filter model is:

```python
@dataclass(frozen=True)
class OptionFilter:
    expiry: ExpiryRange | None = None
    strike: StrikeRange | None = None
    right: OptionRight = OptionRight.BOTH
    limit: int | None = None
```

Expiry filters are relative or absolute. Strike filters can be absolute or spot-relative. Spot-relative
filters may require the strategy/runtime to wait for an underlying quote before choosing concrete
contracts. The subscription lifecycle must make that pending state visible instead of silently subscribing
to an unbounded chain.

### Participant model

Participants remain explicit because combinations such as Massive plus Binance, Massive plus IBKR, and
single-participant option routing are real product choices. A participant is the strategy-facing entity,
such as Massive or Binance. A Market source is the internal concrete data route selected for one target,
such as `massive-equity`, `massive-options`, or `binance-spot`.

Strategy code uses constants:

```python
Participant.MASSIVE
Participant.BINANCE
Participant.OKX
Participant.HYPERLIQUID
Participant.IBKR
```

Participant sets are expressed as:

```python
ParticipantSet.only(Participant.MASSIVE)
ParticipantSet.ALL
```

`ParticipantSet.ALL` means "all configured Market participants that can satisfy this target", not all
providers in the world and not all related markets. Internally this resolves to source IDs at the Market
boundary.

### Internal contract mapping

The Python API may map a static market target to:

```json
{
  "subject": "market:exchange:nasdaq:equity:AAPL",
  "selectors": ["quote", "bar:1m"],
  "source_ids": ["massive-equity", "binance-equity"],
  "params": {
    "market_id": "market:exchange:nasdaq:equity:AAPL"
  }
}
```

An `Options` target may map to a dynamic Market command:

```json
{
  "subject": "options:<underlying>",
  "selectors": ["quote", "greeks"],
  "source_ids": ["massive-options"],
  "dynamic": true,
  "params": {
    "target": "options",
    "underlying_market_id": "...",
    "filter": {
      "expiry_from_unix_nanos": "...",
      "expiry_to_unix_nanos": "...",
      "strike_mode": "around_spot",
      "strike_percent": "0.02",
      "right": "both",
      "limit": 40
    }
  }
}
```

Existing internal `params.mode = "chain"` can be retained as a compatibility path, but it is not the final
product API.

### Ownership

Reference owns:

- canonical instruments, markets, listings, and underlying relationships;
- option contract catalog;
- static expiry, strike, right, and active-status filtering;
- provider symbol mapping when a source needs provider-native symbols.

Market owns:

- subscription lifecycle and owner-scoped release;
- source route selection and source readiness;
- expansion of one market into multiple source-constrained legs;
- dynamic option selection reconciliation;
- observation selector validation and freshness.

Strategy owns only the intent:

- target;
- data selectors;
- allowed sources.

## Consequences

- Strategy code gets one stable mental model: `subscribe(target, data, sources)`.
- Options do not require a separate top-level function. They are a target type with filter semantics.
- `scope` and provider-specific route facts stay behind Reference/Market boundaries.
- The contract and Market runtime must support multiple `source_ids` for static markets and eventually for
  option selections.
- `ParticipantSet.ALL` must query configured sources, not only currently attached runtime sources, so
  demand-driven sources can be discovered before the first subscription starts them.
- Option selections need bounded filters. Unbounded full-chain real-time subscription should require an
  explicit advanced API or an operational limit override.
- Spot-relative strike filters introduce a pending dependency on an underlying price. The first production
  implementation may either require a current underlying quote or expose a pending subscription state until
  one arrives.

## Implementation anchors

- Python Market request types: `kairospy/investment/apps/market/application/requests.py`
- Python Strategy-facing Market facade:
  `kairospy/investment/apps/market/application/application.py`
- Strategy exports: `kairospy/strategy/__init__.py`
- Market control contract: `crates/modules/market/contract/src/control/types.rs`
- Market control handling: `crates/modules/market/src/application/conflux.rs`
- Market subscription resolution: `crates/modules/market/src/application/subscriptions/`
- Market source routing: `crates/modules/market/src/application/sources/subscriptions.rs`
- Reference-to-Market universe current view: `crates/modules/market/src/composition/reference/current view.rs`
- Example strategy: `strategies/print_aapl_multi.py`
