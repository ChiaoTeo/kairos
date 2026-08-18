# IBKR TWS / IB Gateway adapter reference

## Scope

This note covers the in-progress provider-native IBKR execution and Account
migration. The first vertical slice is US equity order entry, order query,
private order/execution events, account snapshot and private account updates
for one explicit TWS or IB Gateway client binding and target account.
Market-data and Reference identity slices remain separate capabilities.

## Official provider sources

- IBKR Campus, TWS API documentation:
  <https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/>
- IBKR TWS API connectivity:
  <https://interactivebrokers.github.io/tws-api/connection.html>
- IBKR TWS API order submission and next-valid order IDs:
  <https://interactivebrokers.github.io/tws-api/order_submission.html>
- IBKR TWS API order modification identity rules:
  <https://interactivebrokers.github.io/tws-api/modifying_orders.html>

Provider facts that constrain the Kairos connection model:

- TWS API is a stateful TCP message protocol to a running TWS or IB Gateway,
  not an HTTP request facade.
- `client_id` distinguishes API clients and affects order visibility and
  modification authority.
- business readiness requires the connection handshake and a valid order-ID
  callback; a connected socket alone is insufficient.
- order IDs are persistent and new IDs must be greater than relevant IDs
  observed from other connected clients.
- modifying or cancelling an API order requires the applicable TWS username
  and client-ID identity.

## Upstream engineering reference

- Repository: <https://github.com/nautechsystems/nautilus_trader>
- Branch inspected: `develop`
- Commit: `eea4e3182f0692e3ae6b013ceb7d8ad68b31818f`
- License: GNU Lesser General Public License v3.0
- Relevant paths:
  - `crates/adapters/interactive_brokers/src/execution/core.rs`
  - `crates/adapters/interactive_brokers/src/execution/core_orders.rs`
  - `crates/adapters/interactive_brokers/src/execution/core_updates.rs`
  - `crates/adapters/interactive_brokers/src/execution/account.rs`
  - `crates/adapters/interactive_brokers/src/common/`
  - `crates/adapters/interactive_brokers/tests/`

Useful upstream behavior includes serializing order-ID allocation/submission,
initializing the local order-ID floor from `nextValidId` plus observed open
orders, keeping client-order/provider-order mappings, consuming order status +
execution + commission updates, bounding request timeouts, and reconnecting a
configured client identity without confusing market-data farm degradation with
the execution socket.

## Kairos mapping

- Integration owns the TWS/Gateway socket, handshake, client-ID allocation,
  order-ID sequence, target-account binding, provider contracts and normalized
  external facts.
- `IbkrAccountQueryConnection`, `IbkrAccountStreamConnection`, `IbkrOrderConnection`,
  `IbkrExecutionStreamConnection`, and `IbkrMarketDataConnection` represent endpoint/client-ID
  bindings. Concurrent roles use distinct client IDs and therefore distinct TCP sessions.
- They directly implement their account, execution, or market capability traits. Each connection
  keeps its library session and subscription objects private and publishes no capability handles.
- Execution owns route ID, canonical instrument association, client order ID,
  durable order lifecycle and reconciliation.
- SMART or another destination is an order-routing fact, not connection
  identity. Target account is distinct from TWS login/client identity.
- a command failure before the encoded order is written is `NotSent`; after a
  possible write it is `Indeterminate` until same-binding reconciliation.

## Deliberately not copied

No NautilusTrader source code has been copied. Kairos does not adopt its cache,
message bus, execution engine, instrument model, Python bindings, Docker
gateway control, global runtime, or application architecture. The upstream
adapter is used only to recover provider behavior and test scenarios. If code
is later reused rather than independently rewritten, preserve its copyright
header, LGPL-3.0 obligations and exact provenance here.

## Current implementation and remaining exit criteria

The Integration participant exposes account-query, account-stream, order, execution-stream, and
market-data connections and no legacy
`IbkrConnection`, `IbkrTwsConnection`, or `IbkrAsync*` public surface. Each owns one client-ID
session on the caller Tokio runtime. Account-bound readiness validates the configured managed
account. The order connection lazily obtains `nextValidId`, scans its open-order set, and sets the
serialized allocator floor to the greater safe value before its first submit. Normalized
query and stream facts use the canonical `ibkr:<order-id>` remote identity and
carry the Integration binding ID. The old blocking order and execution-stream adapters have been
deleted. Query and stream consumers use separate client IDs so each long-running subscription has
one mutable owner.

Before composition, Execution acquires a workspace-owned `ExclusiveProcess`
lease keyed by normalized TWS/Gateway host, port and client ID. The OS advisory
lock is retained for process lifetime and its filename contains only a hash of
the provider identity. This prevents two Execution instances in the same
workspace from claiming the same TWS client identity.
Multi-route composition supports distinct IBKR sessions and rejects duplicate
host/port/client-ID tuples within one process because `ibapi` permits only one
order-update subscription per client session.
Connection, readiness/query and subscription setup have explicit deadlines.
Submit/cancel deadlines begin at the provider call boundary; a timeout there is
classified as `Indeterminate` because `write_all` may have partially or fully
written the framed order before the future was cancelled.

Account server and CLI acquire the same normalized `host:port:client_id`
`ExclusiveProcess` lease before connecting. Account stream deltas merge
`TotalCashValue` and `AvailableFunds` per currency so an available-only update
cannot overwrite authoritative total cash; the complete account snapshot is
the reconnect/recovery baseline.
Production and CLI Account composition use the native async snapshot and
account-update stream, and the old blocking Account synchronization projection
has been removed. Account owns per-segment bootstrap, recovery/resync,
freshness, current view, and publication.

The execution slice is complete only when:

1. ~~provider/principal/target-account/client-ID identities are explicit and
   validated before startup, including `ExclusiveProcess` allocation~~;
2. ~~the async `ibapi` client runs on the business caller's Tokio runtime~~;
3. ~~readiness waits for handshake, managed account validation, next valid order
   ID and initial open-order recovery~~;
4. submit/cancel delivery certainty is fault-tested before/after socket write;
5. query and event capabilities reconcile order status, executions and fills
   with bounded queues, epochs and resync behavior;
6. ~~Execution production composition uses only the async IBKR projections~~; and
7. ~~the migrated IBKR blocking execution path and per-command reconnect model
   are deleted~~.

## Library/session topology audit (2026-08-17)

- Current IBKR Campus documentation defines TWS API as a TCP socket protocol to an already
  authenticated TWS or IB Gateway. `host + port + client_id` identifies one API connection, and a
  TWS/Gateway supports up to 32 API client connections. Source:
  <https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/>.
- One connection carries command/query messages, global notices and logical account/order/market
  subscriptions. Disconnecting that client terminates its ongoing requests/subscriptions without
  affecting other client IDs.
- Local dependency inspection of `ibapi 3.3.0` confirms that its async `Client` owns connect,
  `is_connected`, disconnect and typed subscription APIs. The third-party type remains private to
  Integration.
- The Kairos-owned transport types explicitly own connection, handshake/readiness and reconnect.
  Trading additionally owns next-valid-order-ID allocation and global notices.
- Account, order, and market-data traits are implemented directly by their concrete connections.
  Library subscription objects remain private; composition must not duplicate any individual
  `(host, port, client_id)` lifecycle and must allocate different IDs to concurrent roles.
