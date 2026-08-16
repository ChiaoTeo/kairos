# Domain Taxonomy and Workspace Boundary Remediation

## Status

- Status: implemented; final repository verification recorded below
- Scope: Workspace, Reference, Market, Account, Execution, Integration
- Primary problem addressed: provider-native vocabulary, canonical business
  taxonomy, route identity, and configuration representation were mixed across
  module boundaries.
- Migration policy: migrate one business slice at a time, preserve explicit
  compatibility only at configuration and wire boundaries, and remove each old
  path when its replacement passes the exit criteria.

## Implementation result

Last updated: 2026-08-16.

- Slices 0 through 6 are implemented. The provider matrix and resolved gaps
  are recorded in `docs/domain-provider-taxonomy.md`.
- Execution matches exact participant-scoped access discriminators. OKX keeps
  venue product and `TradingMode` independent and requires an explicit mode
  for margin and derivative routes. `RouteProduct` and wildcard matching are
  removed.
- Reference owns typed `InstrumentKind` and `AssetClass` normalization and
  opaque `ProviderId`/`ProviderProductCode` access facts. Its redundant domain
  `Instrument.product_family` field is removed.
- Market activates sources only from one explicit `MarketDataAccess`; live
  missing or ambiguous access fails closed, while replay remains
  provider-neutral.
- Account owns its registry and explicit segment-to-provider-product/trading
  mode bindings; Integration owns credentials and provider environment
  conventions.
- Reference and Market own their configuration DTOs. Workspace now owns only
  system paths, lifecycle/resources, manifest identity, and generic section
  loading.
- Hyperliquid Spot and OKX Margin Reference coverage are implemented. OKX
  Margin produces canonical Spot identity plus explicit `margin` access.

## Summary

`WorkspaceOkxInstrumentType` is one visible instance of a wider ownership
problem. `kairos-workspace` currently defines provider-specific market source
types, Account business configuration, and provider credential conventions.
Reference and Market then represent overlapping classification knowledge with
unconstrained strings, while Account and Execution independently interpret the
same provider/product aliases.

The most serious consequence is not naming duplication. It is the creation of
false domain equivalences, for example mapping OKX `Swap` to a generic
`UsdMFutures` route and OKX `Futures` to `CoinMFutures`. Contract lifecycle and
settlement/margin denomination are independent dimensions and must not be
collapsed into one enum.

The target design separates four kinds of knowledge:

1. Reference owns canonical asset, instrument, listing, and market facts.
2. Integration owns provider-native request vocabulary and external facts.
3. Account, Execution, and Market own their business commands, state, and route
   identities.
4. Workspace owns paths, process lifecycle, instance resources, and generic
   configuration loading; module composition owns concrete provider selection
   and configuration interpretation.

This work must not introduce a universal product enum. Types should be shared
only when their semantic meaning is genuinely identical across modules.

## Original baseline evidence (resolved by this migration)

This section is retained as the pre-migration diagnosis. Statements using
“currently” describe the original baseline, not the implemented state above.

### Provider knowledge in Workspace

`crates/kairos-workspace/src/workspace.rs` defines:

- `WorkspaceMarketSourceBinding`, including concrete Binance, Massive, OKX, and
  Hyperliquid variants;
- `WorkspaceBinanceDerivativeProduct`;
- `WorkspaceOkxInstrumentType`;
- `WorkspaceHyperliquidMarketType`;
- provider-specific transport choices.

`crates/kairos-workspace/src/account.rs` also:

- stores Account business configuration such as `segments`, `account_model`,
  `initial_balances`, and `fee_rate`;
- knows OKX and Binance environment-variable conventions;
- defaults unknown providers to Binance API key and secret variables.

The configuration files may contain provider names. The boundary violation is
that the Workspace crate owns and interprets provider and business semantics.

### String-based canonical knowledge

Reference domain entities currently use strings for:

- `Asset.asset_class`;
- `Instrument.instrument_type`;
- `Instrument.product_family`;
- `Market.market_type`;
- `Market.asset_type`;
- `ExecutionAccess.product_family`;
- `MarketDataAccess.product_family`.

Provider normalization constructs values such as `spot`, `perpetual`,
`future`, `option`, `swap`, `futures`, `options`, `usd-m-futures`, and
`coin-m-futures` in large match expressions. Some values are canonical
instrument classifications; others are venue product names. Their fields do
not make that distinction explicit.

Market copies `market_type` and `asset_type` into its own `MarketDescriptor`
and relies on string comparisons for source selection and option resolution.
This allows invalid or differently normalized values to cross the Reference to
Market boundary.

### Repeated and conflicting route interpretation

Account and Execution each normalize provider names and maintain product alias
tables. Execution routing interprets provider instrument references through a
third alias table.

Current examples include:

```text
swap            -> UsdMFutures
usd-m-futures   -> UsdMFutures
futures         -> CoinMFutures
coin-m-futures  -> CoinMFutures
option/options  -> Options
```

These aliases combine at least four dimensions:

- provider-native endpoint or `instType`;
- canonical contract lifecycle;
- settlement/margin denomination;
- account trading mode.

The mappings are therefore not a safe cross-provider taxonomy.

## Required semantic model

### Canonical Reference facts

Reference must define the canonical vocabulary needed to describe economic
identity. The exact names should be confirmed with characterization tests, but
the initial model should cover the following distinct concepts.

```rust
pub enum InstrumentKind {
    Equity,
    Spot,
    Perpetual,
    Future,
    Option,
    Index,
}

pub enum AssetClass {
    Fiat,
    Crypto,
    Equity,
}
```

Before adding a canonical `MarketKind`, decide whether a Market describes an
economic market class or a venue-native product surface. If both facts are
needed, model both explicitly rather than overloading `market_type`:

```rust
pub struct Market {
    pub instrument_kind: InstrumentKind,
    pub venue_product: Option<ProviderProductRef>,
    // existing canonical identity and listing fields
}
```

Alternative naming is acceptable, but these invariants are required:

- `Perpetual` and `Future` describe lifecycle, not settlement currency;
- `UsdM` and `CoinM` describe provider product/settlement families, not
  canonical instrument kinds;
- margin trading mode is not an instrument kind;
- provider spellings do not determine canonical identity;
- canonical values are created only by Reference.

`Instrument.instrument_type` and `Instrument.product_family` must receive
separate definitions and invariants. If they remain identical for every
supported instrument, keep one field and delete the other.

### Provider-native Integration facts

Integration must retain provider-owned types such as:

```text
okx::InstrumentType
okx::TradingMode
binance::InstrumentType
binance::ConnectionDomain
massive::MarketType
```

These are not candidates for a shared global enum. They represent request
syntax, endpoint families, connection domains, and provider validation rules.

`ExternalInstrumentKind` remains the normalization boundary between provider
payloads and Reference. It should contain provider-neutral observed facts. A
provider distinction should be added only when Reference genuinely needs it to
construct a different canonical identity.

### Provider access facts

Reference access records should carry explicit provider route information.
Avoid using `product_family: String` when the actual meaning is a provider
request discriminator.

Reference domain must not import Integration application or domain types. The
target shape should use Reference-owned access values, backed by shared value
objects only where the meaning is genuinely cross-module:

```rust
pub struct ProviderAccessRef {
    pub provider_id: ProviderId,
    pub provider_product: Option<ProviderProductCode>,
    pub provider_symbol: ProviderSymbol,
}
```

`ProviderId` and `ProviderProductCode` are opaque validated values, not global
provider taxonomies. They may live in `kairos-domain-types` if Reference,
Market, Execution, and Integration all use exactly the same identity semantics;
otherwise Reference owns them and composition performs the conversion.

At composition time, map this access value explicitly to Integration's
`ParticipantRef`, `ParticipantInstrumentTypeRef`, and provider-native enum.
Neither Reference domain nor its application may import Integration.

`ExecutionAccess` and `MarketDataAccess` may own different access records, but
both must distinguish provider-native routing from canonical product facts.
Provider access must never be inferred by parsing `MarketId`, `InstrumentId`,
`source_symbol`, or `SegmentKey`.

### Account and Execution route identity

`SegmentKey` is an opaque business identity. Values such as `equity-main` and
`isolated-margin-btcusdt` demonstrate that it cannot safely double as a product
enum.

Provider route configuration should be explicit and participant-specific:

```rust
pub enum ExecutionProviderRouteConfig {
    Okx {
        instrument_type: OkxInstrumentType,
        trading_mode: OkxTradingMode,
    },
    Binance {
        domain: BinanceConnectionDomain,
    },
    Ibkr {
        destination: IbkrDestination,
    },
}
```

This is a composition type, not an application or domain type. Application
requests should use business identities and Reference-owned access IDs. The
composition layer resolves those facts to a concrete provider capability.

If Execution needs a provider-neutral business classification, it must be
named for its actual use and must not contain provider settlement products.
For example, a preflight rule may use `DerivativeLifecycle`, while route
selection uses `ProviderAccessRef`.

## Target ownership and dependency flow

```text
workspace.toml / account files / credential files
                    |
                    v
      module-owned composition config DTOs
                    |
          +---------+----------+
          |                    |
          v                    v
  business/domain facts   Integration provider types
          |                    |
          +---------+----------+
                    |
                    v
             concrete capability
```

The crate-level responsibilities are:

### `kairos-workspace`

Keep:

- workspace and instance paths;
- process locks and lifecycle resources;
- generic file access and atomic persistence primitives;
- launch/runtime resource location;
- generic, typed section-loading support that does not know the section's
  business schema.

Remove or relocate:

- provider source enums and endpoint semantics;
- Account business records;
- provider credential environment-variable conventions;
- Reference provider/product schema;
- Market runtime and source schema interpretation.

A possible generic API is:

```rust
pub fn read_section<T: serde::de::DeserializeOwned>(
    &self,
    section: &str,
) -> Result<T, WorkspaceError>;
```

The actual API may instead read a named file. It must not require Workspace to
depend on business or Integration crates.

### Reference

- Domain owns canonical taxonomy and invariants.
- Application exposes canonical queries/results.
- Composition owns provider enablement, endpoints, credentials, and concrete
  source construction.
- Services normalize `ExternalInstrument` facts into canonical entities.
- Contract encodes canonical enums with explicit unknown-value handling.

### Market

- Domain owns observations, subscriptions, freshness, source identity, and
  market-facing selection semantics.
- Composition owns concrete source configuration and mapping from Reference
  access facts to Integration capabilities.
- Market does not recreate Reference taxonomy from strings.

### Account

- Domain owns account model, balances, positions, and segment state.
- Composition owns account binding configuration and conversion to domain
  state/provider connections.
- Initial paper balances and fees are Account/simulation configuration.
- Account application does not receive Workspace records.

### Execution

- Domain owns intent, plan, route identity, and order lifecycle.
- Composition owns provider route configuration and native connection mapping.
- Routing compares explicit participant/access facts rather than aliases.

### Integration

- Owns provider authentication conventions and credentials needed to create a
  provider/principal context.
- Owns exact provider request vocabulary and normalized external facts.
- Does not own canonical Reference identity or business route selection.

## Migration plan

### Slice 0: freeze the vocabulary with characterization tests

Before changing serialized types, create a mapping inventory for every active
provider/product pair:

```text
provider
provider-native instrument/domain
trading mode
external instrument kind
canonical instrument kind
venue product
asset class
settlement asset
market-data access discriminator
execution access discriminator
```

Required providers for the first baseline:

- Binance spot, cross margin, isolated margin, USD-M futures, Coin-M futures,
  options, and equity perpetuals;
- OKX spot, margin, swap, futures, and options;
- Hyperliquid spot and perpetual;
- Massive equity and options;
- IBKR equity execution access.

Exit criteria:

- every currently supported combination is represented;
- ambiguous dimensions are recorded rather than guessed;
- tests capture current canonical IDs and expected new classifications;
- the glossary is reviewed before public contract changes.

### Slice 1: remove false Execution route equivalences

Actions:

1. Stop parsing `ParticipantInstrumentTypeRef` into the current
   `RouteProduct` alias table.
2. Match routes by participant and explicit provider access discriminator.
3. Change OKX composition to retain `InstrumentType` and `TradingMode`
   independently.
4. Change Binance composition to retain `ConnectionDomain` independently.
5. Rename or narrow `RouteProduct`; delete it if no business rule consumes a
   genuinely provider-neutral classification.
6. Remove duplicate alias parsing after all callers use typed configuration.

Exit criteria:

- no mapping equates OKX `Swap` with `UsdMFutures`;
- no mapping equates OKX `Futures` with `CoinMFutures`;
- `SegmentKey` is never parsed to discover provider product;
- route ambiguity and unsupported combinations fail during composition;
- focused routing, order-entry, query, and recovery tests pass.

### Slice 2: introduce the Reference canonical taxonomy

Actions:

1. Add canonical value types to Reference domain or `kairos-domain-types` only
   where semantics are genuinely shared.
2. Convert `ExternalInstrumentKind` to canonical types in one Reference-owned
   normalizer.
3. Replace string fields in Reference entities.
4. Resolve or remove the duplicate `instrument_type`/`product_family`
   distinction.
5. Add validation for impossible field combinations.
6. Update persistence and Reference contract encoding explicitly.

Compatibility policy:

- existing persisted strings may be accepted at the persistence boundary;
- they must be converted immediately to a validated domain type;
- unknown values must produce an explicit error or `Unknown` wire value;
- domain entities must not retain raw legacy strings;
- compatibility code is removed after the stored schema is migrated.

Exit criteria:

- Reference domain cannot construct an unknown instrument or asset class;
- all provider normalizers use the same canonical conversion rules;
- persisted and wire round trips preserve every supported value;
- canonical IDs remain stable unless a separately reviewed identity migration
  requires a change.

### Slice 3: migrate Market to typed Reference facts and access records

Actions:

1. Update the Reference projection consumed by Market.
2. Replace `MarketDescriptor.market_type` and `asset_type` strings with
   explicit types where Market needs those facts.
3. Resolve source activation through `MarketDataAccess`, not exchange/product
   string reconstruction.
4. Move provider source DTOs from Workspace into Market composition.
5. Keep provider-native conversion inside `composition/sources/<provider>.rs`.
6. Delete duplicate matching tables in `composition/mod.rs` and
   `services/reference.rs`.

Exit criteria:

- Market source selection does not infer provider access from canonical IDs or
  symbols;
- one Reference market maps to zero or one explicitly selected market-data
  access, with missing/ambiguous cases rejected;
- Market domain contains no OKX/Binance product enum;
- replay remains provider-neutral and continues through the same Actor input
  path.

### Slice 4: move Account configuration out of Workspace

Actions:

1. Define account binding DTOs in Account composition.
2. Convert account model, balances, fee rates, and segment state into Account
   types at the composition boundary.
3. Keep file location and atomic file operations in Workspace where useful.
4. Move credential records and environment-variable resolution into
   Integration composition or a credential application capability.
5. Reject unknown providers instead of applying Binance defaults.
6. Remove `kairos_workspace::account` after CLI/server/test callers migrate.

Exit criteria:

- Workspace contains no Account balance, fee, model, or segment knowledge;
- provider credentials are resolved by the provider-owning composition path;
- credential debug output continues to redact secrets;
- CLI and server build the same Account application configuration;
- old account registry types have no callers and are deleted.

### Slice 5: move Reference configuration out of Workspace

Actions:

1. Define Reference provider/product/participant DTOs in Reference
   composition.
2. Load the Reference section through a generic Workspace file/section API.
3. Validate provider/product names in Reference composition.
4. Remove `WorkspaceReference*Config` exports.

Exit criteria:

- Workspace does not know Reference providers or products;
- Reference has one configuration parser shared by CLI and server;
- unknown and duplicate source definitions fail before application startup.

### Slice 6: reduce Workspace to system ownership

Actions:

1. Remove `WorkspaceMarketSourceBinding` and provider-specific Workspace
   enums after Market config migration.
2. Remove business manifest structs after all module parsers migrate.
3. Preserve only system-owned manifest fields and generic section access.
4. Add an architecture test preventing provider vocabulary from returning.

Exit criteria:

- `kairos-workspace` has no dependency on business or Integration crates;
- provider names appear only as opaque user data, not Workspace match arms or
  enums;
- Workspace public API contains paths, resources, lifecycle, and generic
  configuration operations only.

## Configuration compatibility strategy

Do not require users to rewrite all configuration files in the first slice.
Preserve old spellings only in module-owned configuration adapters:

```text
old TOML spelling
    -> compatibility parser
    -> validated module configuration
    -> canonical or provider-native type
```

Rules:

- aliases are accepted in one place only;
- aliases never enter domain state;
- serialization emits one canonical spelling;
- deprecated aliases produce diagnostics where practical;
- each alias has a planned removal version or migration milestone;
- no compatibility manager, registry, or second facade is introduced.

## Test plan

### Domain tests

- canonical type parsing accepts only declared values;
- invalid combinations are rejected, including margin as a derivative
  lifecycle or settlement family as an instrument kind;
- Reference normalization produces stable IDs and canonical kinds;
- `InstrumentKind`, venue product, trading mode, and settlement facts remain
  independent.

### Provider mapping matrix tests

For every supported provider product, verify:

```text
provider payload
  -> Integration external facts
  -> Reference canonical entities/access
  -> Market source or Execution route
```

Include negative cases:

- OKX Swap is not classified as Binance USD-M;
- OKX Futures is not classified as Binance Coin-M;
- cross and isolated modes do not change instrument kind;
- an access record for one participant cannot match another participant;
- missing or ambiguous access IDs fail closed.

### Contract and persistence tests

- FlatBuffers encode/decode round trips cover every canonical enum value;
- unknown future enum values are handled explicitly;
- existing Reference SQLite data migrates without changing canonical IDs;
- account and credential TOML round trips preserve supported fields;
- secrets remain redacted from `Debug`, errors, and logs.

### Architecture checks

Add static tests or repository checks for:

```text
kairos-workspace/src must not define provider-specific product enums
kairos-workspace/src must not map provider environment variables
Reference domain classification fields must not be unconstrained String
Market domain must not import provider-native types
Account/Execution application and domain must not import Workspace records
provider aliases may appear only in module composition/config adapters
```

Continue running the repository verification required by `AGENTS.md`:

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

Also run focused searches after each slice:

```text
rg -n "Workspace(Okx|Binance|Hyperliquid|Massive)" crates
rg -n "market_type: String|asset_type: String|product_family: String" crates/business
rg -n '"swap".*"usd-m-futures"|"futures".*"coin-m-futures"' crates/business
rg -n "kairos_workspace::account" crates
```

## Completed deletion checklist

The following obsolete concepts and ownership paths were removed:

- `WorkspaceOkxInstrumentType`;
- `WorkspaceBinanceDerivativeProduct` and provider transport enums;
- `WorkspaceHyperliquidMarketType`;
- `WorkspaceMarketSourceBinding`;
- Workspace-owned Reference config records;
- Workspace-owned Account and credential business records;
- duplicate Account and Execution product alias parsers;
- the current `RouteProduct` variants that encode provider settlement
  products as cross-provider concepts;
- redundant Reference domain `Instrument.product_family` field;
- string-based source matching paths replaced by explicit access IDs.

- `crates/business/execution/service/src/credentials.rs`;
- `crates/kairos-workspace/src/account.rs` and business-owned Workspace path
  helpers.

The v2 FlatBuffers/SQLite field named `product_family` is intentionally kept
as a compatibility boundary. Domain and contract APIs call the fact
`provider_product`; serialization maps it to the old field name. The nullable
legacy instrument product-family slot is read for compatibility and new writes
leave it empty.

## Implemented decisions

Decisions recorded on 2026-08-16:

1. `InstrumentKind::Spot` means a canonical tradable spot instrument. Margin
   is an access/account mode over a spot instrument, never an instrument kind.
2. The existing persisted `Market.market_type` value is venue-native. In
   domain code it is represented by opaque `ProviderProductCode`; the legacy
   field/column name remains temporarily at the SQLite and v2 wire boundary.
3. `Instrument.product_family` carries no independent fact in any supported
   normalizer and is removed from domain state. Its nullable persisted/wire
   slot is read only for compatibility and newly written as null.
4. Reusable opaque participant identity is `ProviderId` in
   `kairos-domain-types`; Integration retains its richer `ParticipantRef` and
   composition performs the explicit conversion.
5. Canonical enum spellings continue through the current string-backed v2
   schema so older readers remain compatible. Persistence decoding validates
   every spelling immediately; a future binary enum change requires a new
   schema version.
6. Provider aliases remain only where they are genuine provider-local input
   compatibility (`okex` and singular/plural option spelling); cross-provider
   aliases and unknown-provider defaults are removed.
7. `SegmentKey` remains opaque. Account persists an explicit mapping from
   segment key to provider product, and Execution route configuration carries
   provider product directly.
8. OKX Execution configuration carries `product` and `trading_mode`
   independently. Spot defaults only to Cash; margin and derivatives require
   an explicit Cross or Isolated mode.

## External provider evidence

Hyperliquid Spot support uses the provider's documented
`spotMetaAndAssetCtxs` info request and keeps it separate from the perpetual
`metaAndAssetCtxs` catalog:

- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot>

## Verification record

Verified on 2026-08-16:

- `cargo test --workspace`: passed. Three explicitly ignored tests remain: two
  million-row acceptance tests and one live Binance credential/network test.
- `uv run pytest -q`: 346 passed, 8 skipped.
- `cargo fmt --all -- --check`: passed.
- `git diff --check`: passed.
- Static ownership scans found no Workspace provider/business type, no
  cross-provider product alias, no `kairos_workspace::account` caller, no
  business-owned provider environment convention, and no cross-module import
  from another business module's `services/`.

Remaining raw strings are intentional boundaries: v2/SQLite compatibility,
CLI/control DTOs, Account JSON persistence, and provider-local configuration.
