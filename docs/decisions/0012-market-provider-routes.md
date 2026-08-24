# Decision 0012: Market Provider Routes and Private Feeds

- Status: Accepted
- Date: 2026-08-24
- Scope: Market contract, Strategy API, observation provenance, provider configuration, replay
- Supersedes: [Decision 0008](0008-market-subscription-targets.md)

## Context

Market exposed runtime source names such as `binance-equity` and
`massive-options` to strategies, configuration, current views, and CLI users.
Those names mixed the data provider, provider-native segment, transport
binding, and runtime feed identity. The generic `ProviderId`, `Participant`,
and `Product` vocabulary also represented unrelated roles in other business
modules.

Users need to state which canonical Market and observations they require. Some
strategies additionally need to prefer, require, or compare data providers.
They do not need to name a REST/WebSocket binding or provider-native product.

## Decision

The Strategy and Market business boundary consists of:

- a typed `MarketTarget`;
- typed `ObservationRequirement` values;
- an optional `ProviderPreference` (`automatic`, `prefer`, `require`, or
  `all_eligible`);
- subscription lifecycle state, satisfied and missing observations, resolved
  providers, and typed pending reasons.

`Provider` means a user-selectable Market/Reference data service provider,
such as `binance` or `massive`. It is distinct from a trading venue, broker,
credential adapter, or runtime feed. Observation and current-view provenance
use `Provider`.

A resolved business route contains exactly the canonical `market_id`, the
`provider`, and supported `observation_kinds`. Provider-native segment,
subscription symbol, transport, credential, endpoint, connection key, and
opaque feed identity are private composition/runtime attachment facts.

Market configuration uses a provider binding list and does not ask users to
invent source names. The runtime may derive opaque feed keys for connection,
epoch, recovery, checkpoint, and diagnostics. Those keys are not accepted by
Strategy APIs or ordinary business CLI commands.

Route eligibility is derived once from the configured adapter capability,
canonical Reference facts, and current readiness. Application and query code
must not infer provider or segment from a source-name prefix, listing symbol,
or a cross-provider `Product` value.

Replay selects a dataset. New manifests record provider provenance plus
dataset identity/version and optional derivation; they do not reuse live feed
IDs. Readers may translate retired source fields only through an explicit
mapping of known legacy values.

Other modules use role-owned identities: Account and Execution use broker or
adapter identities, Capital uses a source authority, and Reference uses an
operational binding identity alongside the shared Market `Provider`.

## Consequences

- Ordinary strategy code can subscribe without knowing provider internals.
- Advanced strategies can express provider constraints without coupling to a
  transport binding.
- Snapshot, freshness, route discovery, Python, Rust, and CLI surfaces share
  the same provider vocabulary.
- A provider may have multiple private bindings while remaining one business
  provider.
- Feed-level control remains possible through operational diagnostics and
  recovery interfaces, not the business contract.
- Adding an adapter requires one capability mapping into resolved routes;
  provider-specific segments stay inside composition/integration.

## Implementation anchors

- Market control contract: `crates/modules/market/contract/src/control/types.rs`
- Resolved route and private attachment: `crates/modules/market/src/domain/market/data_route.rs`
- Provider binding capabilities: `crates/modules/market/src/composition/sources/routing.rs`
- Python intent model: `kairospy/investment/apps/market/application/requests.py`
- Dataset mapping: `kairospy/research/apps/data/application/readers.py`
