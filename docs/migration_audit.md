# Migration Audit

This migration intentionally collapses the previous broad package layout into the current explicit runtime, execution, account, integration, reference, and mode boundaries.

## Current Package Set

The intended top-level `kairospy` packages are:

- `accounts`
- `backtest`
- `context`
- `data`
- `execution`
- `integrations`
- `intents`
- `live`
- `orders`
- `reference`
- `runtime`
- `paper`
- `schema`
- `strategy`
- `surface`

`tests/test_architecture_boundaries_minimal.py` enforces this set.

## Deleted Legacy Domains

The deleted tracked files are concentrated in these old domains:

- `integrations`: old connectors, acquisition, data products, ports, and extension trees are replaced by smaller driver/exchange/broker/payload-adapter modules.
- `runtime`: old kernel, daemon, live registry, recovery, store, and profile trees are replaced by `StrategyRuntime`, `ModeRunner`, `RunProfile`, and mode engines.
- `surface`: old CLI command tree is replaced by focused product modules under `kairospy.surface.products`.
- `data`: old catalog/storage/live/replay/quality split is replaced by compact `DataStore`, stream, ingest, query, and id modules.
- `execution`: old command/outbox/router/policy/fill/order trees are replaced by `ExecutionCoordinator`, execution state, simulation adapter, and live execution adapter.
- `reference`: old access/contracts/factory/repository/sync modules are replaced by provider-neutral catalog, model, serde, store, refresh, universe, and corporate-action modules.
- `strategy`: old protocols/runtime/intents naming is replaced by `strategy.protocol`, `strategy.control`, and `strategy.views`.
- `analytics`, `governance`, `identity`, `infrastructure`, `market`, `portfolio`, `products`, `research`, `risk`, and `workspace`: these were broad cross-cutting or product-specific domains in the previous architecture. They are not part of the current core package set.

## Preserved Capabilities

The current tree keeps the capabilities needed for the core architecture:

- Strategy event loop and runtime views: `kairospy.runtime`
- Strategy-facing protocol and controls: `kairospy.strategy`
- Intent journal and intent states: `kairospy.intents`
- Order model and order journal: `kairospy.orders`
- Account snapshots, projections, reservations, and ledger: `kairospy.accounts`
- Intent-to-order/fill coordination and state: `kairospy.execution`
- Simulated account/backtest result/metrics: `kairospy.backtest`
- Paper composition over backtest/runtime primitives: `kairospy.paper`
- Live gateway, reconciliation, private stream collection, and runtime state: `kairospy.live`
- Provider-neutral reference model/store/refresh/universe: `kairospy.reference`
- Provider drivers, exchanges, brokers, and payload adapters: `kairospy.integrations`
- Data store/query/stream primitives: `kairospy.data`
- CLI product surface: `kairospy.surface`

## Explicitly Deferred Capabilities

The following old capabilities should not be reintroduced under their former broad package names:

- Large governance/readiness/promotion workflow
- Full research capture and validation framework
- Full option pricing, volatility surface, and advanced analytics stack
- Full portfolio/treasury/risk engine
- workspace/project product shell
- Legacy live daemon and deployment manifests
- Old connector/data-product catalog tree

If these capabilities are rebuilt, they should be introduced as narrow modules behind the current boundaries, with tests that prove dependency direction.

## Boundary Evidence

Current validation commands:

```bash
.venv/bin/python -m compileall -q kairospy
.venv/bin/pytest tests/test_architecture_boundaries_minimal.py -q
.venv/bin/pytest tests -q
```

Current architecture guards verify:

- The deprecated trading package path is absent.
- The deprecated trading coordinator name is absent from runtime/product/docs code.
- Execution names live in `kairospy.execution`.
- Accounts do not import provider payload code.
- Live orchestration uses payload adapters instead of provider imports.
- Reference does not import runtime/provider layers.
- Top-level package layout matches the current architecture.
