# Integration capability matrix

## 1. Purpose

This document is the evidence index for provider coverage. It deliberately
distinguishes a concrete adapter implementation from a business-composed path
and from behavior verified against a live provider.

This matrix is the maintained source for implementation and certification
status. Future work belongs in tasks or issues rather than in this document;
status changes require the evidence defined below.

Last audited: 2026-08-25.

## 2. Status vocabulary

| Status | Meaning | Required evidence |
|---|---|---|
| `I` | Implemented | A concrete participant connection implements the named Integration capability and has focused fixtures/tests. |
| `C` | Composed | A business module has a configured route to the concrete connection and consumes normalized Integration facts. |
| `R` | Recovery-tested | Disconnect, replay/resubscribe, sequence gap, response loss, or reconciliation behavior is covered as applicable. |
| `L-ro` | Live read-only certified | A credentialed or public live read was recorded without mutation. |
| `L-tx` | Live transaction certified | A testnet/demo or explicitly authorized minimal transaction lifecycle was recorded and reconciled. |
| `-` | Not applicable | The provider does not own this capability. |
| blank | Not proven | Evidence is missing or the slice is incomplete. |

`I` does not imply `C`; `C` does not imply `L-ro` or `L-tx`. A source file,
constructor, SDK dependency, or endpoint wrapper alone is not `I`.

## 3. Provider and product coverage

### 3.1 Binance

| Product | Reference | Market query | Market stream | Account query/stream | Order command/query/stream | Certification | Notes |
|---|---|---|---|---|---|---|---|
| Spot | I/C | I/C | I/C/R | I/C/R | I/C/R | L-ro | Public market subscribe, receive, reconnect/restore and unsubscribe certified 2026-08-25. Shared command is submit/cancel; concrete REST also has keep-priority quantity reduction and symbol-scoped cancel-all. |
| Cross/isolated margin | | I | I | I/C/R | I/C/R | | Concrete cross-margin trade history and symbol-scoped cancel-all exist; isolated parameters and account controls remain explicit gaps. |
| USD-M Futures | I/C | I/C | I/C/R | I/C/R | I/C/R | L-ro | Current market-route mark-price subscribe, receive, reconnect/restore and unsubscribe certified 2026-08-25. Concrete modify, bounded batch submit/cancel, and symbol-scoped cancel-all exist. |
| COIN-M Futures | I/C | I/C | I/C/R | I/C/R | I/C/R | L-ro | Public quote subscribe, receive, reconnect/restore and unsubscribe certified 2026-08-25. Separate endpoint/fault domain with concrete modify, bounded batch submit/cancel, and scoped cancel-all. |
| Options | I/C | I/C | I/C/R | I/C/R | I/C/R | L-ro | Current public/market route quote and mark-price subscribe, receive, reconnect/restore and unsubscribe certified 2026-08-25. Greeks, typed trade/fee history, bounded batch submit/cancel and scoped cancel-all exist. |
| Equity/Stocks Trading | I/C | I/C | I/C/R | | I/C/R | | API-key-gated product; not a canonical exchange identity source. |
| Alpha Trading | I | I | I | | | | No business composition route proven. |
| Portfolio Margin | | | | I | I | | Concrete methods/resources exist without a complete business route. |
| Portfolio Margin Pro | | | | I | | | Account-only slice. |
| Algo/TWAP/VP | | | | | provider-native only | | Inherent methods only; no shared capability or current business owner. |
| Copy Trading | | | | | provider-native only | | Read-only inherent methods. |
| Institutional Loan | | | | provider-native only | | | No composed funding/credit application use case. |
| Funding wallet | | | | I/C | | | Current use is an account snapshot; asset-transfer command/status capabilities are not implemented. |
| Simple Earn Flexible/Locked | | | | semantic model only | | | Product, quota, rate component, position, reward, subscribe/redeem, and reconciliation semantics exist; no concrete connection yet. |
| BFUSD/RWUSD | | | | semantic model only | | | Classified as yield-bearing assets rather than forced into Flexible. |
| ETH/SOL/On-chain/Soft Staking | | | | semantic model only | | | Common Earn lifecycle is available where applicable; wrap/claim/configuration remain Binance-native. |
| Dual Investment/Discount Buy | | | | provider-native only | | | Conditional payoff requires a future structured-investment model and Risk caller. |

Remaining Binance production gates:

- quota scheduling beyond observed request-weight/order-count headers;
- live verification of server time offset and timestamp rejection recovery;
- live snapshot/diff continuity certification for every depth stream family;
- isolated-margin history parameters and product-specific funding-wallet history;
- explicit test-account validation and `L-tx` evidence.

### 3.2 Hyperliquid

| Product | Reference | Market query | Market stream | Account query/stream | Order command/query/stream | Certification | Notes |
|---|---|---|---|---|---|---|---|
| Perpetual | I/C | I/C | I/C/R | I / I | I / I / I | | Concrete exchange API adds modify and bounded batch submit/modify/cancel; shared capability and business composition are not claimed. |
| Spot | I/C | I/C | I/C/R | I / I | I / I / I | | Explicit asset-index mapping plus concrete modify/batch operations; business composition remains separate. |
| HIP-3 DEX | partial | partial | partial | | | | Prefix-aware candle query exists; complete catalog, account, collateral, and execution routing are unproven. |
| Subaccounts | | | | partial | partial | | Address/vault execution identity is not composed. |
| Vaults | | | | | | | Provider endpoints are not a current Account/Execution capability. |

Remaining Hyperliquid production gates:

- live snapshot plus buffered private-event recovery certification;
- agent wallet/master account/address validation;
- market-order slippage policy before market orders are enabled;
- scheduled cancel-all and live indeterminate reconciliation;
- subaccount/vault execution identity;
- testnet `L-tx` evidence.

### 3.3 Massive

| Product | Reference | Historical query | Market stream | Account | Execution | Certification | Notes |
|---|---|---|---|---|---|---|---|
| US Equities | I/C | I/C | I/C/R | - | - | L-ro | Preserves exchange/tape/TRF evidence; Massive is not the canonical venue. |
| US Options | I/C | I/C | I/C/R | - | - | L-ro | Reference coverage is explicitly bounded by underlying; typed contract snapshot exposes partial Greeks/IV/OI with provider observation time. |
| Futures | I | I | I | - | - | | Independent `/futures/v1` REST and Futures WebSocket connections; composition and live certification are intentionally separate. |
| Indices | provider-native I | I | I | - | - | | Typed native catalog plus REST aggregates and dedicated `V/A/AM` socket; shared Reference has no index kind. |
| Forex | I | I | I | - | - | | Typed spot catalog, bars/quotes and dedicated `C/CA/CAS` socket; no trade feed is claimed. |
| Crypto | I | I | I | - | - | | Typed spot catalog, bars/quotes/trades and dedicated `XQ/XT/XA/XAS` socket. |
| Flat Files | - | decision | - | - | - | | Explicitly deferred to a Workspace-owned resumable S3 download plus Market-owned streaming ingest; no ownerless REST facade. |

Remaining Massive production gates:

- live verification of entitlement-aware error classification and diagnostics;
- live entitlement certification for Futures, Indices, Forex, and Crypto;
- live entitlement and recency certification for the implemented options snapshot/Greeks query;
- additional Reference-owned corporate-action use cases such as splits;
- Flat Files checksum, schema, resume, and scale validation.

### 3.4 OKX

| Product | Reference | Market query | Market stream | Account query/stream | Order command/query/stream | Certification | Notes |
|---|---|---|---|---|---|---|---|
| Spot | I/C | I/C | I/C/R | I/C/R | I/C/R | | Concrete private REST adds single/batch amend plus batch submit/cancel with per-item outcomes. |
| Margin | I/C | I/C | I/C/R | I/C/R | I/C/R | | Explicit account controls are incomplete. |
| Swap | I/C | I/C | I/C/R | I/C/R | I/C/R | | WS derivative facts plus concrete single/batch amend and batch submit/cancel. |
| Expiry Futures | I/C | I/C | I/C/R | I/C/R | I/C/R | | REST typed mark/index/funding/OI snapshots and ordinary order extensions are implemented. |
| Options | I/C | I/C | I/C/R | I/C/R | I/C/R | | WS Greeks normalization exists. |
| Algo/Business WS | | partial | | | | | Must remain a distinct concrete connection family from ordinary private WS. |

Remaining OKX production gates:

- live certification of server timestamp/signature health;
- immediate scoped cancel-all (OKX exposes batch cancel and a distinct dead-man switch, not an equivalent endpoint);
- richer semantic categorization of bill subtypes only when an audit caller owns that vocabulary;
- explicit leverage/margin/position-mode commands;
- Business WebSocket/Algo only after an Execution business model exists;
- demo `L-tx` evidence.

## 4. Capability implementation inventory

The tables below describe participant-neutral capabilities, not every
provider-native inherent method.

### 4.1 Reference and market

| Capability | Binance | Hyperliquid | Massive | OKX |
|---|---|---|---|---|
| `InstrumentCatalogQuery` | Spot, USD-M, COIN-M, Options, Equity, Alpha | Info Spot/Perpetual | Equity/Options/Futures/Forex/Crypto; Indices provider-native | Spot/Margin/Swap/Futures/Options |
| `MarketQuoteQuery` | Spot, futures, Options, Equity, Alpha | all mids | | ticker endpoint |
| `MarketTradeQuery` | Spot, futures, Options, Alpha | | historical only | recent trades |
| `MarketBarQuery` | Spot, futures, Options, Alpha | historical capability only | historical capability only | candles |
| `MarketOrderBookQuery` | Spot, futures, Options, Alpha | L2 book | | books |
| `MarketMarkPriceQuery` | USD-M/COIN-M macro-backed families | Info | | REST + stream |
| `MarketIndexPriceQuery` | USD-M/COIN-M macro-backed families | | | REST + stream |
| `MarketFundingRateQuery` | USD-M/COIN-M macro-backed families | Info | | REST + stream |
| `MarketOpenInterestQuery` | USD-M/COIN-M macro-backed families | Info | | REST + stream |
| `MarketGreeksQuery` | Options | | Options REST snapshot | REST + stream |
| `MarketTickerQuery` | | | | stream only |
| `MarketStatusQuery` | | | | stream only |
| `HistoricalBarQuery` | Spot | Info candle snapshot | Equity/Options/Futures/Indices/Forex/Crypto | |
| `HistoricalQuoteQuery` | Spot returns unsupported | | Equity/Options/Futures/Forex/Crypto | |
| `HistoricalTradeQuery` | Spot | | Equity/Options/Futures/Crypto | |
| `MarketSubscriptionCommand` / stream | Spot, Margin, USD-M, COIN-M, Options, Equity, Alpha | unified public/user socket | Equity/Options/Futures/Indices/Forex/Crypto sockets | public socket |

An empty cell means no implementation was proven in the 2026-08-25 audit. A
stream normalizer is not counted as the corresponding bounded query.

### 4.1 Provider subscription-planning audit

The 2026-08-25 audit applies Decision 0018 without claiming uniform production readiness:

| Provider | Dedicated component | Current planning/recovery evidence | Availability conclusion |
|---|---|---|---|
| Binance | Product-specific connections with a shared private Binance policy | endpoint-class routing, stream deduplication, capacity shards, pacing, server inventory reconciliation, replacement-first reconnect and public live certification | Spot, USD-M, COIN-M and Options public streams are `L-ro`; authenticated and depth-continuity gates remain. |
| OKX | Dedicated public WebSocket connection | typed channel arguments, per-argument acknowledgements, restore, bounded buffering and order-book sequence recovery | Implemented/composed/recovery-tested, but not live-certified in this audit. |
| Hyperliquid | Dedicated unified WebSocket connection | typed native subscriptions, acknowledgement handling, restore and bounded public/private event buffering | Implemented/composed/recovery-tested, but not live-certified in this audit. |
| Massive | Dedicated product WebSocket connections | product-specific feed parameters, acknowledgement handling, restore and bounded buffering | Equities/Options retain existing `L-ro`; other entitled products are not live-certified. |
| IBKR | Dedicated market-data session, query delivery only | bounded quote query over the IBKR session; no `MarketSubscriptionCommand` implementation | Usable only as a polling/query source; push subscription planning is not implemented. |

The concrete OKX, Hyperliquid and Massive connections already satisfy the provider-specific ownership
boundary, so no empty cross-provider planner or wrapper was added. Capacity, traffic-class or
reconciliation policy should be added inside the owning provider connection only when its current
protocol and production evidence require it.

### 4.2 Account and execution

| Capability | Binance | Hyperliquid | Massive | OKX |
|---|---|---|---|---|
| `AccountQuery` | Spot, Margin, USD-M, COIN-M, Options, Funding, Portfolio | perpetual + spot combined snapshot | - | balance + positions + open orders |
| `AccountMarketProfileQuery` | | | - | fees + account config |
| `AccountCredentialQuery` | | | - | account config inspection |
| `AccountStream` | main product user sockets | unified socket | - | private socket |
| `OrderCommand` | main products, Equity, Portfolio | exchange REST | - | private REST |
| `OrderQuery` | main products, Equity, Portfolio | account Info REST | - | private REST |
| `ExecutionStream` | main product user sockets | unified socket | - | private socket |
| `EarnProductQuery` / `EarnCommand` / `EarnActionStatusQuery` | | | - | |
| `AssetTransferCommand` / `AssetTransferStatusQuery` | | | - | |

## 5. Composition inventory

| Business module | Binance | Hyperliquid | Massive | OKX |
|---|---|---|---|---|
| Reference | Spot, USD-M, COIN-M, Options, Equity | Spot, Perpetual | Equity, bounded Options coverage | all public instrument families |
| Market | Spot, Equity, USD-M, COIN-M, Options | Spot, Perpetual | Equity, Options | Spot, Swap, Futures, Options |
| Account | Spot, Margin, USD-M, COIN-M, Options, Funding | not composed; Integration capability only | - | Trading Account |
| Execution | Spot, Margin, USD-M, COIN-M, Options, Equity | not composed; Integration capability only | - | ordinary trading |

Hyperliquid business composition is intentionally outside this proposal. Phase 1 evidence covers
the Integration capability; recovery and transaction certification remain separate gates.

## 6. Certification record requirements

Every future `L-ro` or `L-tx` mark must be backed by an adapter-reference note
that records:

- provider environment and retrieval date;
- product family and endpoint/transport;
- credential type and required permission, never the credential value;
- exact behavior exercised;
- response/error classification observed;
- reconciliation result for a mutation;
- known entitlement, geographic, VIP, or account-mode restrictions.

Live checks are opt-in and must not run in the default workspace test suite.

## 7. Phase 0 verification gate

Phase 0 is complete only when all commands below are green on a stable
worktree, or an exact unrelated pre-existing failure is recorded:

```text
cargo test -p kairos-integration
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

The matrix itself must also be updated in the same change whenever a
provider/product gains or loses an `I`, `C`, `R`, `L-ro`, or `L-tx` status.
