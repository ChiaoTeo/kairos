# System Architecture Plan

This document defines the intended role of `kairospy.application.system` and the migration path from the current runtime injection style to a system-owned trading runtime lifecycle.

## Background

KairosPy currently has clear low-level boundaries:

- `kairospy.core` owns stable trading domain models.
- `kairospy.application.runtime` owns the deterministic strategy event loop and runtime processors.
- `kairospy.application.service.runtime` implements runtime ports for account, market data, execution, and reference.
- `kairospy.application.service.modes` parses mode-specific configuration and assembles backtest, paper, and live runs.
- `kairospy.application.system` owns operational run artifacts such as account registries, daemon state, run registry, and account journals.

Earlier migrations moved runtime startup behind `TradingSystemLauncher`, but the system package is still shaped by the order features were added. Mode recipes still know some operational system types, and `application.system` mixes facade, runtime hosting, run control, account identity, and artifact persistence in broad folders.

The target architecture should make those responsibilities explicit and keep long-lived resource ownership and run lifecycle orchestration in `application.system`.

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

## Current Structural Problem

`application.system` has started to own the right responsibilities, but the current package shape is not orthogonal enough:

- `system.run` mixes run identity, daemon supervision, output logging, persisted artifacts, live runtime state, and account journals.
- `system.trading` mixes the public launcher, internal runtime host, lifecycle hooks, and runtime resource specifications.
- `service.modes` no longer imports `application.system`; account selection is currently treated as mode configuration parsing, while startup, artifacts, and live state resource construction stay in system.
- Daemon foreground execution and direct foreground execution both run through `TradingSystemLauncher`, but daemon status writing remains a separate procedural flow.
- Several files are named after implementation mechanics (`spec.py`, `system.py`, `state.py`) instead of the system capability they provide.

The symptom is a folder tree that looks like a grab bag. The deeper issue is that `system` currently combines five separate axes without naming them clearly:

1. **Facade**: user-facing entry points for starting or inspecting a trading run.
2. **Runtime host**: construction and lifecycle of `RuntimeKernel` / `RuntimeRunSession`.
3. **Run control**: daemon/background launch, stop commands, state, current instance, heartbeat.
4. **Artifacts**: logs, summaries, normalized config, metrics, journals.
5. **Resources**: account identity, credentials, connection lifecycle, resumable state stores, and runtime port bundles.

The next migration should separate these axes before adding more Hyperliquid paper/live behavior.

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

## System Facade Decision

`system` should be the external facade. `service` should not be exposed as the product API.

Rationale:

- A caller wants to run, stop, list, inspect, and recover trading processes. Those are system behaviors, not service behaviors.
- Services are capability implementations: market data, account, execution, reference. They are dependencies of a run, not the owner of a run.
- `surface`, CLI, tests, and future app/workspace code should depend on one stable facade: `kairospy.application.system`.

The public API should remain narrow:

```python
from kairospy.application.system import TradingSystemLauncher
```

Additional public facades can be added only when there is a separate external workflow:

```python
from kairospy.application.system import RunControl
from kairospy.application.system import AccountDirectory
```

Do not export internal runtime-host types from `application.system.__all__`.

## Resource Model Decision

Account identity, credentials metadata, connection lifecycle, and resumable state should be treated as system resources.

This matches how mature trading systems separate concerns:

- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) describes Python as the control plane for strategy logic, configuration, and orchestration, with modular adapters for venues. That implies external venues and orchestration-time state are assembled outside the deterministic strategy runtime.
- [Hummingbot architecture](https://hummingbot.org/blog/hummingbot-architecture---part-1/) centers the system around a clock that drives connectors and strategies. Its market connectors own exchange/network operations and account/order tracking, while strategies consume those capabilities.
- [Hummingbot GitHub](https://github.com/hummingbot/hummingbot) describes exchange connectors as standardized REST/WebSocket interfaces, including venues that require API keys or wallet keys. That makes credentials and connections operational resources.
- [Freqtrade](https://github.com/freqtrade/freqtrade) presents the bot as an exchange-connected process controlled by API/UI, and its [REST API docs](https://www.freqtrade.io/en/2023.6/rest-api/) expose bot control, system info, trade inspection, and WebSocket messages as operational concerns outside the strategy code.

For KairosPy this means:

- Account config is a resource: it selects account identity, venue, credentials, initial cash, currency, and fee defaults.
- Connections are resources: they have lifecycle, health, stop behavior, and eventually reconnect policy.
- Live runtime state is a resource: it is resumable operational state owned by the system, not by the deterministic runtime.
- Runtime port bundles are host resources: they are the narrowed capabilities handed from system host to `RuntimeKernel`.
- Artifacts are not resources: they are outputs/sinks produced by a run.

## Proposed Package Structure

```text
kairospy/application/system/
  __init__.py

  facade/
    trading.py          # public launcher/facade implementation
    run_control.py      # optional future facade for list/stop/start daemon

  host/
    runtime_host.py     # owns RuntimeKernel/RuntimeRunSession construction
    lifecycle.py        # prepare/complete hooks
    resources.py        # system-owned runtime resource bundle

  control/
    daemon.py           # foreground/background process control
    registry.py         # run discovery/list/stop command lookup
    state.py            # run state, current instance, heartbeat payloads

  artifacts/
    writer.py           # summary/config/metrics writer
    logging.py          # stdout/stderr tee
    journals/
      account.py        # persisted account journal sink

  resources/
    connections.py      # connection lifecycle abstraction
    live_state.py       # resumable live execution/private-stream state
    accounts.py         # concrete paper/live account and execution resource bundles
```

This is the target shape, not a request to move every file at once. Initial implementation can keep compatibility shims if needed, but new code should follow the target vocabulary.

Mapping from current files:

| Current | Target | Reason |
| --- | --- | --- |
| `trading/launcher.py` | `facade/trading.py` | Launcher is external facade, not a trading subdomain. |
| `trading/system.py` | `host/runtime_host.py` | It hosts runtime; naming it `system.py` hides its specific role. |
| `trading/spec.py` | `host/resources.py` plus host run request type | Resource bundles are host internals. |
| `trading/lifecycle.py` | `host/lifecycle.py` | Lifecycle hooks are runtime-host behavior. |
| `run/daemon.py` | `control/daemon.py` | Daemon is run control, not artifact persistence. |
| `run/registry.py` | `control/registry.py` | Registry is run discovery/control state. |
| `run/state.py` | `resources/live_state.py` | Live restoration state is a run resource; daemon state belongs under control when extracted. |
| `run/artifacts.py` | `artifacts/writer.py` | Summary/config/metrics are artifacts. |
| `run/logging.py` | `artifacts/logging.py` | Output capture is an artifact concern. |
| `run/journals/account.py` | `artifacts/journals/account.py` | Account journal is persisted run artifact. |
| `accounts/registry.py` | removed for now | Account selection moved to mode config parsing; move credential lookup to system resources later only when workspace/account requirements are concrete. |
| `connections/manager.py` | `resources/connections.py` | Connections are resources because they must be started, stopped, and inspected by the system. |

## Core Types

### `TradingRuntimeResources`

`TradingRuntimeResources` is the runtime-host bundle of capabilities passed into `RuntimeKernel`.

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

The object is intentionally host-level, but it should expose only stable external capabilities. Runtime processor bundles, account journals, and state stores are assembled inside system.

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

## State And Outputs

`RunOutput` lives in `application.system.artifacts.output` because system owns run instance output coordination.

`AccountCurrentProjector` and `TimelineProjector` live in `application.system.projectors` because they turn runtime views and steps into system-managed outputs.

`RunProjectionCatalog` and `RunProjectionService` also live in `application.system.projectors`. They are the read-side facade for run instance projections: surface/CLI asks this service what datasets exist and how to load them, instead of hard-coding artifact filenames or decoding timeline view snapshots itself.

Runtime does not receive artifact output objects. It emits runtime lifecycle facts through `RuntimeStep`, and system projectors decide how those steps and views are persisted.

Live runtime state stores currently live in `application.system.resources.live_state` because restore/save is a resumable run resource. This keeps the old broad `system.run` package out of the implementation tree.

In the target package shape:

- The concrete `RunOutput` lives in `application.system.artifacts.output`.
- The low-level `RunInstanceStore` lives in `infrastructure.artifacts`.
- Runtime must not receive `RunOutput` or any artifact sink.
- System projectors, projection catalog, and projection read service should remain under `application.system.projectors`.
- Surface should depend on the projection service for run instance inspection; it should not own run artifact filename contracts.
- Live restoration state should be owned by the runtime host lifecycle. Its concrete JSON file store may live under `system.host` or `system.artifacts` depending on whether the file is treated as resumable operational state or an inspectable artifact.

## Planning Rules

Use these rules before moving code:

1. A module under `system.facade` may import `service.modes`, `system.host`, `system.control`, and `system.artifacts`.
2. A module under `system.host` may import `runtime`, runtime ports, `system.resources`, and run output coordination. It should not import CLI/surface modules.
3. A module under `system.control` may import facade launchers when it needs to start a foreground child target, but daemon bookkeeping should be independent of trading host internals.
4. A module under `system.artifacts` should not import `service.modes`; it serializes already-produced runtime/results data.
5. A module under `service.modes` must not import `application.system`. It may parse account selectors from config, but concrete system resources must be created by system.
6. `runtime` must never import `system`.

These rules are more important than the exact folder names. A move is only useful if it improves these dependency directions.

## Migration Plan

### Phase 1: Name The Axes Without Changing Behavior

- Add target packages without compatibility re-export modules: `facade`, `host`, `control`, `artifacts`.
- Move `RunOutputLog` and `RunOutput` under `system.artifacts`.
- Move `TradingSystem`, `TradingRunSpec`, `TradingRuntimeResources`, and lifecycle hooks under `system.host`.
- Keep `application.system.TradingSystemLauncher` as the only public trading run facade.
- Delete old import paths instead of keeping compatibility shims. Internal callers must import concrete modules under the new axes.

### Phase 2: Refine Resource Ownership

- Keep connection lifecycle and resumable live state under `system.resources`.
- Keep concrete account/execution construction in mode-specific system resource bundles. Current mode recipes may parse account config, but should move toward account requirements rather than concrete account service construction when workspace-level account ownership becomes clearer.
- Make `ConfiguredPaper` / `ConfiguredLive` recipes describe account requirements and resource settings, while the system facade performs operational account lookup when credentials and workspace state matter.
- `LiveRuntimeStateStore` creation belongs in system facade/host; mode config should only describe the desired state path.

### Phase 3: Consolidate Run Control

- Introduce a small `RunControl` class that owns `start_background`, `run_foreground`, `request_stop`, and `list`.
- Move daemon helper functions into focused collaborators:
  - `RunIdentityFactory`
  - `RunStateStore`
  - `RunCommandStore`
  - `RunProcessLauncher`
- Keep behavior simple. Do not introduce an actor framework or async supervisor until real reconnect/heartbeat requirements force it.

### Phase 4: Connection Ownership

- Add concrete connection managers only when paper/live need long-lived supervision beyond the current stream iterator.
- Paper and live should share market feed connection lifecycle code.
- Live can add private stream/broker lifecycle later without changing runtime contracts.

### Phase 5: Tighten Tests Around Boundaries

- Add architecture tests for forbidden imports:
  - `service.modes` should not import `application.system.host`, `application.system.control`, or `application.system.artifacts`.
  - `system.artifacts` should not import `service.modes`.
  - `runtime` should not import `application.system`.
- Add behavior tests around foreground paper:
  - run records are created under workspace `.kairos/runs`.
  - `run.log` captures strategy stdout.
  - stop command is honored for live paper streams.

### Run Directory Layout

Direct config runs write artifacts to the configured run directory:

```text
<runs_root>/<mode>/<run_id>/
```

Daemon-managed runs use that path as a run group and isolate each launch under a run instance:

```text
<runs_root>/<mode>/<run_id>/
  current.json
  state.json
  summary.json
  events.jsonl
  run.lock
  instances/
    <run_instance_id>/
      state.json
      summary.json
      events.jsonl
      command.json
      daemon.log
      run.log
      normalized_config.json
      account/
      live_state.json
```

The group files are pointers and mirrors for status/list commands. The instance directory owns the real launch artifacts, stop command, account journal, and live restore state.

## Current Migration State

### Done

- `application.system.host.resources.TradingRuntimeResources` exposes only stable runtime capabilities.
- `application.system.host.resources.TradingRunSpec` describes system-owned runtime assembly.
- `application.system.host.runtime_host.TradingSystem` owns kernel/session construction, account journals, lifecycle hooks, and connection start/stop.
- `application.system.facade.trading.TradingSystemLauncher` is the common entry point for config runs and JSONL event runs.
- `application.system` lazily exports `TradingSystemLauncher` as the public facade.
- `application.system.facade.trading.TradingSystemLauncher` is the implementation of the trading run facade.
- `application.system.facade.run_control.RunControl` is the public facade for daemon start, foreground run, stop requests, and run listing.
- `application.system.host` owns runtime host, host resources, and lifecycle hooks.
- `application.system.control` owns daemon process control and run registry.
- `RunDaemonService` keeps foreground/background lifecycle orchestration while `_RunTargetResolver` owns config target resolution and `_RunDaemonStore` owns daemon state/current/summary/event writes.
- Background daemon start uses a lightweight target descriptor for run id and configured run directory; only the foreground worker resolves the full configured target and constructs runtime resources.
- Background daemon launch passes the parent-created run instance id into the foreground worker, so registry state has one active/completed instance per launch instead of a stale launch placeholder plus a worker instance.
- Daemon start claims a run group under an exclusive `run.lock`, rejects a second fresh active instance for the same `mode/run_id`, and permits a new instance after the current one stops, fails, or is abandoned as stale.
- Daemon artifacts are isolated under `<runs_root>/<mode>/<run_id>/instances/<run_instance_id>`; group-level `current.json`, `state.json`, `summary.json`, and `events.jsonl` are status mirrors.
- `application.system.artifacts` owns run summaries and output logs.
- `RunOutput` owns common run artifact files including summary, normalized config, metrics, optional legacy JSONL, current state snapshots, and runtime history records.
- `application.system.trading` and `application.system.run` compatibility entry points were removed.
- `ConfiguredBacktest`, `ConfiguredPaper`, and `ConfiguredLive` are mode recipes: they parse config and expose mode requirements, but they do not start system or construct runtime account/execution resources.
- `JsonLiveRuntimeStateStore` lives in `application.system.resources.live_state`.
- `ConfiguredLive` exposes `state_path`; `TradingSystemLauncher` constructs the live state store and performs restore/save through a system lifecycle object.
- `ConfiguredBacktest` exposes account/backtest/execution config values; `BacktestAccountResources.from_configured(...)` constructs the simulated account, backtest account service, execution service, and coordinator before runtime hosting.
- `ConfiguredPaper` exposes account/paper/execution config values; `PaperAccountResources.from_configured(...)` constructs the paper account and execution services before runtime hosting.
- `ConfiguredLive` exposes account/live/execution config values; `LiveAccountResources.from_configured(...)` constructs the live broker, account service, execution service, and coordinator before runtime hosting.
- Backtest/paper/live run result assembly that depends on account runtime resources lives with `BacktestAccountResources` / `PaperAccountResources` / `LiveAccountResources`, not in mode recipes.
- `ConnectionManager` lives in `application.system.resources.connections`.
- `service.modes` no longer imports `application.system`; account config selection lives in `application.service.modes.common.accounts` until resource requirements become concrete enough to move without adding a generic factory.
- `RuntimeRunSpec` remains narrow and does not expose business service dependencies or processor injection.
- Surface run startup commands call `application.system` launchers and catch `TradingConfigurationError` for recipe/resource configuration failures; strategy/runtime failures propagate as execution errors. Surface does not import mode recipe modules, mode-specific configuration errors, or `application.system.control` internals.
- Daemon foreground runs call the same system launchers used by surface.

### Remaining

- Move account selection and credential lookup further out of mode recipes, so recipes describe account requirements and system resources perform resolution.
- Continue reducing `TradingSystemLauncher` by extracting live lifecycle and artifact orchestration only if concrete duplication appears.
- Continue keeping `RunControl` thin; deeper daemon lifecycle, registry, and process launch collaborators stay under `system.control` until duplication or external workflows require more public API.
- Avoid introducing a generic account resource factory until the three mode-specific resource bundles show real duplication that a named abstraction can remove.
- Move more result assembly into `application.system.facade` or a host result assembler only if meaningful duplication appears.
- Add concrete `PaperConnectionManager` / `LiveConnectionManager` when real stream supervision moves out of services.
- Prefer adding dependencies to `TradingSystemLauncher.__init__` before adding more module-level launcher functions.

## Acceptance Criteria

The migration is complete when:

- `ConfiguredBacktest`, `ConfiguredPaper`, and `ConfiguredLive` no longer construct or call `TradingSystem`.
- `RunOutput` is only referenced by system-level artifacts, projectors, factories, or specs.
- `JsonLiveRuntimeStateStore` is not under `application.service.modes`.
- `RuntimeRunSpec` no longer exposes concrete runtime service dependencies.
- No module under `application.runtime` imports `application.system`.
- Surface code calls `application.system.TradingSystemLauncher` for run startup.
- Full test suite passes.

Useful checks:

```bash
rg -n "RuntimeRunSpec\\(" kairospy tests
rg -n "RunOutput|JsonLiveRuntimeStateStore" kairospy tests
rg -n "application.system|RunOutput|append_history|update_current" kairospy/application/runtime
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
