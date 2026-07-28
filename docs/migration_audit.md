# Migration Audit

This migration intentionally replaces the previous flat package layout with a strategy-runtime axis: strategy API, runtime orchestration, core domains, run modes, integrations, data, and surface.

## Current Package Set

The intended top-level `kairospy` packages are:

- `context`
- `core`
- `data`
- `integrations`
- `modes`
- `runtime`
- `service`
- `strategy`
- `surface`

`tests/test_architecture_boundaries_minimal.py` enforces this set.

## Deleted Legacy Domains

Old top-level domain and mode packages are removed:

- `accounts`
- `backtest`
- `execution`
- `intents`
- `live`
- `market`
- `orders`
- `paper`
- `reference`

The deleted or relocated tracked files are concentrated in these old domains:

- `integrations`: old connectors, acquisition, data products, ports, and extension trees are replaced by smaller driver/exchange/broker/payload-adapter modules.
- `runtime`: old kernel, daemon, live registry, recovery, store, and profile trees are replaced by `RuntimeKernel`, `RuntimeRunner`, `RunProfile`, and mode engines.
- `surface`: old CLI command tree is replaced by focused product modules under `kairospy.surface.products`.
- `data`: old catalog/storage/live/replay/quality split is replaced by compact `DataStore`, stream, ingest, query, and id modules. Market-specific request orchestration lives in `kairospy.application.service.domains.market`.
- `service`: use-case orchestration that composes core domains with stores, gateways, and provider-neutral adapters. Domain services live directly under `kairospy.application.service.domains.account`, `kairospy.application.service.domains.market`, and `kairospy.application.service.domains.reference`. Mode assembly lives under `kairospy.application.service.modes.*`. Operational run/daemon assembly lives under `kairospy.application.service.operations.*`.
- `execution`: old command/outbox/router/policy/fill/order trees are replaced by `kairospy.core.execution` coordination/state definitions plus `kairospy.application.service.domains.execution` live/simulated adapters and persistence.
- `reference`: old access/contracts/factory/repository/sync modules are replaced by `kairospy.core.reference` catalog/model/identity/product/participant definitions plus `kairospy.application.service.domains.reference` store, serialization, refresh, universe selection, and corporate-action services.
- `strategy`: old protocols/runtime/intents naming is replaced by `strategy.protocol`, `strategy.events`, and strategy-facing re-exports over `context.control` and `core.views`.
- `schema`: ambiguous market schema modules are replaced by `kairospy.core.market` models, subscriptions, row encoders, runtime market projections, and `kairospy.core.views` shared view schemas.
- `analytics`, `governance`, `identity`, `infrastructure`, `portfolio`, `products`, `research`, `risk`, and `workspace`: these were broad cross-cutting or product-specific domains in the previous architecture. They are not part of the current core package set. The new `kairospy.application.service.domains.market` package is a narrow market data orchestration layer, not a reintroduction of the old broad market domain.

## Preserved Capabilities

The current tree keeps the capabilities needed for the core architecture:

- Strategy event loop, runtime projections, and runtime views: `kairospy.application.runtime`
- Market observations, quote/book/bar/trade/rate models, row encoders, and subscriptions: `kairospy.core.market`
- Market data specs, resolution, historical read/download coordination, live persistence, and replay: `kairospy.application.service.domains.market`
- Account bootstrap/reconciliation/live private stream orchestration: `kairospy.application.service.domains.account`
- Reference catalog refresh and lifecycle-event sync orchestration: `kairospy.application.service.domains.reference`
- Backtest config-to-engine/source assembly: `kairospy.application.service.modes.backtest`
- Live config-to-engine/broker/source assembly: `kairospy.application.service.modes.live`
- Run and daemon cross-mode assembly: `kairospy.application.service.operations.run`
- Strategy-facing protocol and controls: `kairospy.application.strategy`
- Intent journal and intent states: `kairospy.core.intent`
- Order model and order journal: `kairospy.core.order`
- Account snapshots, account state derivation, and ledger: `kairospy.core.account`
- Order reservations and buying-power checks: `kairospy.core.execution`
- Intent-to-order/fill coordination, state, and execution-current runtime projection: `kairospy.core.execution`
- Shared view schema, registry, store, and hashing primitives: `kairospy.core.views`; concrete runtime view payloads live under `kairospy.application.runtime.projection.*`
- Simulated account/backtest result/metrics: `kairospy.application.mode.backtest`
- Paper composition over backtest/runtime primitives: `kairospy.application.mode.paper`
- Live gateway, reconciliation, private stream collection, and runtime state: `kairospy.application.mode.live`
- Provider-neutral reference model, universe query/result, and catalog definitions: `kairospy.core.reference`
- Reference store/serialization/refresh/snapshot builders, catalog transitions, universe selection, and corporate-action services: `kairospy.application.service.domains.reference`
- Provider drivers, exchange connectors, broker gateways, data providers, and provider-specific payload/lifecycle adapters: `kairospy.infrastructure.integrations`
- Data store/query/stream primitives: `kairospy.infrastructure.data`
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
- The deprecated schema package path is absent.
- Old top-level domain and mode packages are absent.
- The deprecated trading coordinator name is absent from runtime/product/docs code.
- Execution names live in `kairospy.core.execution`.
- Shared view primitives live in `kairospy.core.views`; runtime view payload dataclasses live under `kairospy.application.runtime.projection.*`; core packages do not import `kairospy.application.strategy`.
- Accounts do not import provider payload code.
- Live orchestration uses payload adapters instead of provider imports.
- Reference does not import runtime/provider layers.
- Top-level package layout matches the current architecture.
