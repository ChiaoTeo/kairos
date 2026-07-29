# System Architecture Plan

This document defines the intended role of `kairospy.application.system` and the migration path from the current runtime injection style to a system-owned trading runtime lifecycle.

## Background

KairosPy currently has clear low-level boundaries:

- `kairospy.core` owns stable trading domain models.
- `kairospy.application.runtime` owns the deterministic strategy event loop and runtime processors.
- `kairospy.application.service.runtime` implements runtime ports for account, market data, execution, and reference.
- `kairospy.application.service.modes` parses mode-specific configuration and assembles backtest, paper, and live runs.
- `kairospy.application.system` owns operational run artifacts such as account registries, daemon state, run registry, and account journals.

The remaining issue is that mode configuration objects still directly construct `RuntimeRunSpec` with runtime services and operational sinks. This makes `RuntimeRunSpec` act as a broad dependency injection container instead of a runtime execution contract.

The target architecture should move long-lived resource ownership and run lifecycle orchestration into `application.system`.

## References

The direction is consistent with established trading frameworks:

- NautilusTrader defines a shared `system` kernel for backtest, sandbox, and live environments. Its system layer initializes components, owns lifecycle, manages shared resources, and keeps the core event loop deterministic.
- Hummingbot uses a central clock and connector model: connectors own exchange/network interactions, while strategies consume stable trading capabilities.
- Freqtrade and Hummingbot API both show the need for a separate operational layer when managing bot instances, daemon status, stop commands, and multi-run operations.
- `~/Code/kairos_v2/kairos-system` has the same split: orchestrator, reactor, venue systems, workers, commands, and ticks live in system; strategy/execution/framework are separate consumers of system events.

## Design Goals

1. `runtime` remains deterministic and does not own external connection lifecycle.
2. `service.runtime` implements runtime ports, but does not know run directories, daemon state, or user-facing schemas.
3. `service.modes` parses configuration and builds mode recipes; it should not directly start runtime.
4. `system` owns trading runtime assembly, connection lifecycle, state restore/save, journals, daemon records, and stop handling.
5. Backtest, paper, and live should share one system run path, with mode-specific resource factories.
6. Surface APIs should call system-level launchers instead of assembling runtime internals.

## Dependency Direction

```text
surface
  -> application.system
      -> application.service.modes
      -> application.service.runtime
      -> application.runtime
      -> infrastructure.integrations
      -> core

application.runtime
  -> core
  -> application.strategy

application.service.runtime
  -> application.runtime.ports
  -> application.service.domain
  -> infrastructure.integrations
  -> core
```

Forbidden directions:

- `application.runtime` must not import `application.system`.
- Runtime processors must not import concrete services.
- Service implementations must not import surface.
- Domain services must not import runtime processors or system run artifacts.

## Layer Responsibilities

### `application.runtime`

Runtime owns the strategy event loop:

- event envelopes and event lines
- runtime kernel/session
- processors
- runtime ports
- deterministic ordering of strategy, views, controls, intents, and processor publication

Runtime should receive already-built ports and processors from system. It should not decide where account journals are written, how live state is restored, how WebSocket tasks are restarted, or how a daemon record is updated.

### `application.service.runtime`

Runtime-facing services adapt domain behavior or integration clients to runtime ports:

- `AccountPort`
- `MarketDataPort`
- `TradingExecutionPort`
- `ReferencePort`

These services may depend on domain services and infrastructure clients. They should stay reusable across backtest, paper, and live where behavior is shared.

### `application.service.modes`

Mode services should become configuration recipes:

- parse TOML and validate mode-specific settings
- resolve strategy references
- normalize user config
- provide mode-specific resource requirements
- create simple mode recipe objects

They should avoid directly creating `RuntimeRunSpec` or calling `RuntimeRunner.run_sync`.

### `application.system`

System owns the lifecycle of a trading node/run:

- run identity and run directories
- account registry selection
- resource factories
- connection lifecycle
- state restore/save
- account journal sinks
- daemon foreground/background execution
- stop command binding
- heartbeat/status events
- runtime construction and disposal
- result assembly

This is the layer that should know the difference between a one-shot backtest, a paper run with live market data, and a live run with private account streams.

## Proposed Package Structure

```text
kairospy/application/system/
  accounts/
    registry.py

  connections/
    __init__.py
    manager.py
    health.py

  resources/
    __init__.py
    spec.py
    factory.py

  run/
    __init__.py
    daemon.py
    registry.py
    session.py
    state.py
    journals/
      account.py

  trading/
    __init__.py
    result.py
    spec.py
    system.py
    launcher.py
```

Initial implementation can keep some modules small. The point is to establish ownership boundaries before adding more live connectivity.

## Core Types

### `TradingRuntimeResources`

`TradingRuntimeResources` is the system-owned bundle of runtime capabilities.

```python
@dataclass(frozen=True, slots=True)
class TradingRuntimeResources:
    source: RuntimeEventLine | None
    data: MarketDataPort | None
    account: AccountPort | None
    reference: ReferencePort | None
    trading_execution: TradingExecutionPort | None
    connections: ConnectionManager | None
```

The object is intentionally system-level, but it should expose only stable external capabilities. Runtime processor bundles, account journals, and state stores are assembled inside system.

### `TradingRunSpec`

`TradingRunSpec` describes a configured trading run.

```python
@dataclass(frozen=True, slots=True)
class TradingRunSpec:
    run_id: str
    mode: RuntimeMode
    strategy: Strategy
    resources: TradingRuntimeResources
    run_directory: Path
    normalized_config: Mapping[str, object]
    lifecycle: TradingLifecycle | None = None
```

### `TradingSystem`

`TradingSystem` is the internal common executor for backtest, paper, and live. Public callers should use `TradingSystemLauncher`.

```python
class TradingSystem:
    def __init__(self, spec: TradingRunSpec) -> None:
        ...

    def run(self) -> TradingRunResult:
        ...
```

### `TradingSystemLauncher`

`TradingSystemLauncher` is the public system facade for trading runs.

```python
class TradingSystemLauncher:
    def run_backtest_config(self, config_path: str | Path) -> BacktestRunResult:
        ...

    def run_paper_config(self, config_path: str | Path) -> PaperRunResult:
        ...

    def run_live_config(self, config_path: str | Path) -> LiveRunResult:
        ...

    def run_events(self, *, strategy_path: str, events_path: str | Path, ...) -> RuntimeRunResult:
        ...
```

Its lifecycle:

```text
prepare
  restore state
  start connections
  refresh account snapshot when needed
  construct runtime kernel/session

run
  consume event source
  publish views/intents/controls
  write journals through runtime processors

finalize
  save state
  stop connections
  flush sinks
  assemble run result
```

## Runtime Contract

`RuntimeRunSpec` is intentionally narrow. It describes a runtime replay/run request, not the full business dependency graph.

Current shape:

```python
RuntimeRunSpec(
    run_id,
    mode,
    strategy,
    source,
    pre_events=(),
    started_at=None,
)
```

The system layer constructs `RuntimeKernel` / `RuntimeRunSession` with account, market data, execution, reference, and journal resources. `RuntimeRunSpec` should not expose those dependencies.

## Connection Lifecycle

`ConnectionManager` is a small system protocol:

```python
class ConnectionManager(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def health(self) -> Mapping[str, object]:
        ...
```

`TradingSystem` starts the connection manager before runtime execution and stops it in `finally`. Later this can expand to async lifecycle:

```python
async def start_async(self) -> None: ...
async def stop_async(self) -> None: ...
```

Initial implementation:

- `NoopConnectionManager` for runs with no long-lived external connection lifecycle.

Future implementations:

- `PaperConnectionManager` for market feed lifecycle.
- `LiveConnectionManager` for market feed, broker private stream, heartbeat, and reconnect state.

The manager should own WebSocket/background tasks. Runtime services should expose data through ports but not supervise reconnect loops themselves.

## State And Journals

`RunAccountJournal` belongs in `application.system.run.journals` because it writes run artifacts.

`AccountJournalProcessor` belongs in `application.runtime.processors` because recording account views is runtime-internal behavior.

`AccountJournalSink` belongs in `application.runtime.ports` because runtime only needs a sink contract.

Live runtime state stores should move from mode services to `application.system.run.state` because restore/save is run lifecycle behavior.

## Current Migration State

### Done

- `application.system.trading.spec.TradingRuntimeResources` exposes only stable runtime capabilities.
- `application.system.trading.spec.TradingRunSpec` describes system-owned runtime assembly.
- `application.system.trading.system.TradingSystem` owns kernel/session construction, account journals, lifecycle hooks, and connection start/stop.
- `application.system.trading.launcher.TradingSystemLauncher` is the common entry point for config runs and JSONL event runs.
- `application.system` lazily exports `TradingSystemLauncher` as the public facade.
- `ConfiguredBacktest`, `ConfiguredPaper`, and `ConfiguredLive` are mode recipes: they parse config and assemble resources, but they do not start system.
- Mode recipes expose `build_result(runtime)` for mode-specific result assembly.
- `JsonLiveRuntimeStateStore` lives in `application.system.run.state`.
- `ConnectionManager` lives in `application.system.connections`.
- `RuntimeRunSpec` remains narrow and does not expose business service dependencies or processor injection.
- Surface run commands call `application.system` launchers.
- Daemon foreground runs call the same system launchers used by surface.

### Remaining

- Move more result assembly into `application.system.trading.result` only if meaningful duplication appears.
- Add concrete `PaperConnectionManager` / `LiveConnectionManager` when real stream supervision moves out of services.
- Prefer adding dependencies to `TradingSystemLauncher.__init__` before adding more module-level launcher functions.

## Acceptance Criteria

The migration is complete when:

- `ConfiguredBacktest`, `ConfiguredPaper`, and `ConfiguredLive` no longer construct or call `TradingSystem`.
- `RunAccountJournal` is only referenced by system-level factories or specs.
- `JsonLiveRuntimeStateStore` is not under `application.service.modes`.
- `RuntimeRunSpec` no longer exposes concrete runtime service dependencies.
- No module under `application.runtime` imports `application.system`.
- Surface code calls `application.system.TradingSystemLauncher` for run startup.
- Full test suite passes.

Useful checks:

```bash
rg -n "RuntimeRunSpec\\(" kairospy tests
rg -n "RunAccountJournal|JsonLiveRuntimeStateStore" kairospy tests
rg -n "application.system" kairospy/application/runtime
uv run pytest
```

## Open Design Questions

1. Should `TradingSystemLauncher` return existing mode-specific result types or a unified `TradingRunResult` with mode-specific details?
2. Should live connection management be synchronous first, or should system introduce an async lifecycle immediately?
3. Should paper mode use the same live connection manager as live mode when it consumes real-time market data?
4. Which dependencies should be injected into `TradingSystemLauncher` first: credential resolution, connection management, or artifact writing?

Recommended defaults:

- Keep existing mode-specific result types in the first migration.
- Start with synchronous lifecycle methods and add async once real WebSocket supervision is moved.
- Share live market connection logic between paper and live.
- Route daemon and surface through a common `TradingSystemLauncher`.
