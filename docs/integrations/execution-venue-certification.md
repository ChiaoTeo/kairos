# Execution venue certification

This is the maintained evidence matrix for exchange-facing order execution. It separates deterministic
adapter behavior from a transaction exercised against a provider environment. Public market-data
certification does not certify order execution.

Last audited: 2026-08-26.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `D` | Deterministic provider fixtures prove request mapping and Confirmed/Rejected/Indeterminate classification. |
| `C` | Execution composition routes typed commands to the adapter and applies normalized lifecycle facts. |
| `R` | Response loss, private-event recovery, query reconciliation, or restart convergence is proven for the route. |
| `T` | A demo, testnet, paper, or explicitly authorized minimal transaction lifecycle is recorded and reconciled. |
| blank | The evidence has not been established. |

Levels are cumulative only when each earlier column is explicitly present. `D/C/R` evidence from local
fixtures is not `T` evidence.

## Current matrix

| Provider | Execution channel | Submit/cancel outcome | Composition | Recovery | Transaction certification | Current conclusion |
| --- | --- | --- | --- | --- | --- | --- |
| Binance | Spot | D | C | R |  | Deterministic/composed; testnet transaction not certified. |
| Binance | Margin | D | C | R |  | Deterministic/composed; isolated and cross-margin live modes require separate evidence. |
| Binance | USD-M Futures | D | C | R |  | Deterministic/composed; testnet transaction not certified. |
| Binance | COIN-M Futures | D | C | R |  | Deterministic/composed; testnet transaction not certified. |
| Binance | Options | D | C | R |  | Deterministic/composed; testnet transaction not certified. |
| Binance | Equity/Stocks Trading | D | C | R |  | API-key-gated route; no authorized transaction record. |
| OKX | Spot/Margin | D | C | R |  | Deterministic/composed; demo transaction not certified. |
| OKX | Swap/Futures/Options ordinary order path | D | C | R |  | Shared ordinary-order path exists; product-specific demo evidence is absent. |
| Hyperliquid | Spot/Perpetual | D |  |  |  | Adapter command/query/stream exists but Execution composition is not established. |
| IBKR | TWS/Gateway | D |  |  |  | Send completion is classified Indeterminate pending callbacks/query; Execution route is not certified. |
| Simulated | Local simulated venue | D | C | R | not applicable | Execution Core reference route, not evidence for an external venue. |

The broad `R` marks for composed Binance and OKX routes refer to deterministic application/provider
recovery tests: uncertain command results retain commitment, block duplicate dispatch, and require an
authoritative event or query to converge. They do not claim that every provider product has passed a live
socket-disconnect exercise.

## Transaction certification record

Promoting one row to `T` requires a durable provider-specific record containing:

- provider environment, product/channel, account mode, and date;
- credential permission class without credential values;
- one stable client order identity from submit through private event and bounded query;
- accepted, partial/full fill, cancel or rejection, and fee/quantity mapping as applicable;
- response-loss or disconnect recovery with no duplicate order;
- restart from the persisted Execution snapshot and final reconciliation result;
- cleanup result and any residual order, position, balance, or fee impact.

The transaction must be opt-in and use demo/testnet/paper infrastructure where the provider supports it.
No default workspace test may place an external order.

## Evidence anchors

- Provider capability and composition inventory: [capability matrix](capability-matrix.md)
- Outcome rules and application recovery: `crates/modules/execution/src/integration_tests.rs`
- Binance order mapping and outcome fixtures:
  `crates/platform/integration/src/services/participants/binance/execution.rs`
- OKX order mapping and outcome fixtures:
  `crates/platform/integration/src/services/participants/okx/execution.rs`
- Hyperliquid order mapping and outcome fixtures:
  `crates/platform/integration/src/services/participants/hyperliquid/exchange.rs`
- IBKR callback-delivery semantics:
  `crates/platform/integration/src/services/participants/ibkr/execution.rs`

Status changes must link new reproducible evidence. A new adapter, route configuration, or public
read-only session alone does not change this matrix.
