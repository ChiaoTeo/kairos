# Domain and Provider Taxonomy Baseline

## Status and purpose

- Status: implemented baseline; all four recorded migration gaps are closed
- Applies to: Reference, Market, Account, Execution, Integration composition
- Companion design:
  `docs/domain-taxonomy-and-workspace-boundary-remediation.md`

This file freezes the semantic dimensions used during the boundary migration.
It is not a universal provider enum. Each row records how provider-native
vocabulary maps to normalized external facts, canonical Reference facts, and
provider access discriminators.

## Vocabulary

### Canonical instrument kind

An economic identity owned by Reference:

- `equity`: an ownership security;
- `spot`: a directly traded spot instrument;
- `perpetual`: a derivative with no expiry;
- `future`: an expiring futures contract;
- `option`: an option contract;
- `index`: a non-tradable or separately accessed index instrument.

`margin` is not a canonical instrument kind. Margin is an access/account mode
for an instrument.

### Venue product

A product surface exposed by one venue or provider. Examples are OKX `swap`,
Binance `usd-m-futures`, and Binance `coin-m-futures`. Venue products may be
stored in Reference access facts, but they do not define canonical instrument
kind.

### Trading mode

An order/account constraint such as cash, cross margin, or isolated margin.
Trading mode is independent from instrument kind and venue product.

### Settlement family

The asset or denomination used for margin and settlement. `USD-M` and `COIN-M`
belong here. Settlement family is independent from perpetual versus future.

### Segment key

An opaque Account/Execution business route identity. It is never parsed to
derive any of the dimensions above.

### Provider access discriminator

An opaque provider-owned code used to select a concrete Integration
capability. Current examples include `spot`, `swap`, `usd-m-futures`, and
`equity`. Its meaning is scoped by participant identity.

## Provider mapping matrix

`N/A` means the capability is not currently represented by that pipeline. A
question mark is an explicit migration gap and must not be replaced by a
guess.

| Provider capability | Native instrument/domain | Trading mode | External kind | Canonical kind | Venue product | Asset class | Settlement | Market-data discriminator | Execution discriminator |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Binance Spot | `InstrumentType::Spot` / `ConnectionDomain::Spot` | cash | `Spot` | `spot` | `spot` | crypto | quote asset | `spot` | `spot` |
| Binance Cross Margin | `ConnectionDomain::CrossMargin` | cross | N/A catalog; uses spot instruments | `spot` | `cross-margin` access | crypto | quote/borrow asset | spot market access | `cross-margin` |
| Binance Isolated Margin | `ConnectionDomain::IsolatedMargin` | isolated | N/A catalog; uses spot instruments | `spot` | `isolated-margin` access | crypto | symbol-scoped assets | spot market access | `isolated-margin` |
| Binance USD-M perpetual | `InstrumentType::UsdMFutures` / `ConnectionDomain::UsdMFutures` | contract | `Perpetual` | `perpetual` | `usd-m-futures` | crypto | quote stablecoin from payload | `usd-m-futures` | `usd-m-futures` |
| Binance USD-M equity perpetual | `InstrumentType::UsdMFutures` | contract | `EquityPerpetual` | `perpetual` with equity underlying | `usd-m-futures` | equity underlying / crypto settlement | quote stablecoin from payload | `usd-m-futures` | `usd-m-futures` |
| Binance Coin-M perpetual/future | `InstrumentType::CoinMFutures` / `ConnectionDomain::CoinMFutures` | contract | `Perpetual` or `Future` from payload | `perpetual` or `future` | `coin-m-futures` | crypto | base coin from payload | `coin-m-futures` | `coin-m-futures` |
| Binance Options | `InstrumentType::Option` / `ConnectionDomain::Options` | contract | `Option` | `option` | `options` | crypto | payload settlement asset | `options` | `options` |
| Binance Equity | broker equity capability | cash | `Equity` | `equity` | `equity` | equity | provider/payload currency | `equity` | `equity` |
| OKX Spot | `InstrumentType::Spot` | `Cash` | `Spot` | `spot` | `spot` | crypto | quote asset | `spot` | `spot` |
| OKX Margin | `InstrumentType::Margin` | explicit `Cross` or `Isolated` | `Margin` | `spot`; no `margin` canonical kind | `margin` access | crypto | provider account configuration | `margin` | `margin` |
| OKX Swap | `InstrumentType::Swap` | normally `Cross` or `Isolated` | `Perpetual` | `perpetual` | `swap` | crypto | payload settlement asset; never inferred as USD-M | `swap` | `swap` |
| OKX Futures | `InstrumentType::Futures` | normally `Cross` or `Isolated` | `Future` | `future` | `futures` | crypto | payload settlement asset; never inferred as COIN-M | `futures` | `futures` |
| OKX Options | `InstrumentType::Option` | explicit provider-supported mode | `Option` | `option` | `options` | crypto | payload settlement asset | `options` | `options` |
| Hyperliquid Perpetual | provider perpetual universe | provider account mode | `Perpetual` | `perpetual` | `perpetual` | crypto | USDC/default only when provider omits quote | `perpetual` | N/A |
| Hyperliquid Spot | provider spot universe | cash | `Spot` | `spot` | `spot` | crypto | payload token/quote facts | `spot` | N/A |
| Massive Equity | `InstrumentType::Equity` / `MarketType::Equity` | N/A | `Equity` | `equity` | `equity` | equity | USD/payload quote | `equity` | N/A |
| Massive Options | `InstrumentType::Option` / `MarketType::Option` | N/A | `Option` | `option` | `options` | equity underlying | USD/payload quote | `options` | N/A |
| IBKR Equity execution | broker execution capability | account-defined | N/A Reference catalog in current route composition | `equity` resolved by Reference access | provider destination is separate from product | equity | account currency | N/A | `equity` |

## Required invariants

1. Participant identity scopes every provider access discriminator.
2. OKX `swap` is never normalized to Binance `usd-m-futures`.
3. OKX `futures` is never normalized to Binance `coin-m-futures`.
4. `Perpetual` and `Future` come from contract lifecycle facts, not settlement
   currency.
5. `Cross` and `Isolated` do not change canonical instrument kind.
6. `SegmentKey` is not a source of provider, product, trading mode, symbol, or
   settlement facts.
7. Reference creates canonical IDs; Integration does not receive or construct
   them.
8. Market and Execution select provider access by explicit access facts and do
   not reconstruct it from canonical IDs or symbols.
9. Unknown provider vocabulary fails at composition or normalization; it does
   not fall back to Binance or another provider.
10. Configuration aliases are accepted only by one provider-specific config
    parser and are converted immediately to typed values.

## Closed characterization and migration gaps

### GAP-1: Hyperliquid Spot Reference coverage

Closed. Integration exposes separate Spot and Perpetual catalog capabilities;
Reference normalizes both and publishes explicit access records. Spot uses the
documented `spotMetaAndAssetCtxs` request.

### GAP-2: OKX Margin canonical/access representation

Closed. The default OKX Reference set includes `InstrumentType::Margin`.
Normalization creates canonical Spot identity while markets and access records
retain provider product `margin`. Execution requires TradingMode separately.

### GAP-3: Execution access installation

Closed. Execution server and direct CLI page through active Reference
`ExecutionAccess` rows and install exact participant/product/symbol mappings
before accepting live submissions. Unsupported providers fail closed.

### GAP-4: Venue product field meaning

Closed. Canonical lifecycle is `InstrumentKind`; venue product is the opaque
`ProviderProductCode`. Domain access APIs name it `provider_product`. Existing
SQLite and v2 wire fields named `market_type`/`product_family` remain explicit
compatibility boundaries and are validated on decode.

## Characterization test ownership

- Integration tests prove provider payload to `ExternalInstrumentKind` and
  provider-native discriminator mapping.
- Reference provider tests prove external facts to canonical identity and
  access facts.
- Market composition tests prove Reference market/access to concrete source
  mapping.
- Execution routing tests prove exact participant plus provider discriminator
  matching and fail-closed behavior.
- Account composition tests prove provider-native account/trading mode mapping
  without parsing `SegmentKey`.

Each closed gap above has a focused regression test in its owning module.
