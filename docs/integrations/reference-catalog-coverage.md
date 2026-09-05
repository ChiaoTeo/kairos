# Reference catalog sources and coverage

Reference catalog support describes what an Integration adapter can fetch and what a configured
Reference source can prove. It does not imply that Market can subscribe to live observations or
that Execution and an account can trade the resulting instrument.

Connection facts and catalog intent have separate owners:

- Integration connection profiles own provider, product, environment, endpoints, credential
  references, enabled state, and the `reference-catalog` purpose.
- The Reference source registry owns desired state, provider-product binding, coverage scope,
  completeness, freshness, and synchronization policy.
- System starts one workspace Reference process and supplies its resources. It does not select
  provider products or duplicate the source registry.

## Current source families

| Source family | Provider products | Credential | Declared catalog semantics |
| --- | --- | --- | --- |
| Binance | spot, margin, USD-M futures, COIN-M futures, options | Public products use the built-in public connection unless an Integration profile overrides the endpoint | Exchange-native product catalog. A successful full source scan proves only the configured Binance product scope. |
| OKX | spot, margin, swap, futures, options | Public products use the built-in public connection unless an Integration profile overrides the endpoint | Exchange-native product catalog. Options are complete only after every advertised underlying or family in the configured scope succeeds. |
| Hyperliquid | exchange-native products supported by its adapter | Public | Exchange-native product catalog within the adapter's declared product scope. |
| Massive | equities and explicitly scoped options underlyings | Integration connection with credential reference | Provider product membership plus source fields that the adapter can independently map. It is not a complete list of US issuers, US listings, or US execution venues. |

Each concrete source definition records its exact fact kinds and scope. Query code may return
`not_found_in_covered_scope` only when a usable, complete coverage contains the requested scope.
Partial or scoped membership, a successful page, and the mere presence of a connection are not
proof of absence.

## Provenance and licensing

Provider-specific endpoint provenance, inspected upstream implementations, test evidence, and
license obligations remain in [adapter provenance](adapter-provenance/README.md). Catalog facts
retain their source identity and provider record identity in Reference persistence; credentials
and complete raw provider payloads are not copied into catalog events or logs.

### Derivative settlement evidence

Binance's [exchangeInfo schema](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
labels `marginAsset` as a margin asset, not an independent settlement assertion. The generic derivative
parser therefore leaves settlement unknown instead of relabeling that field. A product-specific settlement
mapping still requires applicable contract evidence and is not yet implemented. For example, the
[ETHBTC contract specification](https://bin.bnbstatic.com/static/cms/cg08ou2ak0tn7mcplvfg/file/69378ac91c2b26ed6e7f878372c34b6cc7d1b3e2371be5cd69c14df216916be3.pdf)
sections 7–8 specify BTC quotation and settlement for that contract; this is not authority to infer every
contract's settlement from a product name, quote currency or account collateral. Sources checked 2026-09-06.

## Adding a source

A new catalog source normally requires independent changes in two owners:

1. Integration implements or reuses the typed instrument-catalog query, validates pagination and
   full-scan termination, and normalizes provider facts.
2. Reference adds a private provider-product binding, declares fact kinds, scope and completeness,
   maps only supported assertions into canonical records, and adds promotion, failure and coverage
   tests.

The public Reference contract changes only when a new closed business fact or setup goal cannot be
expressed by the existing source definition. System composition remains provider-neutral unless
the provider requires a genuinely new platform resource type.
