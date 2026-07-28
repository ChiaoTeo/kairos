# Runtime Layering

This document defines the `kairospy.application.runtime` boundary. Runtime is a small mode-neutral kernel; backtest, paper, live, services, and surfaces assemble inputs around it.

## Runtime Responsibility

`kairospy.application.runtime` is the run kernel. It owns the mechanics that are true for every run mode:

- one run-scoped data plane: `RuntimeDataEnvelope`
- ingestion and ordering of runtime events
- strategy hook dispatch
- runtime-owned projections and strategy-readable views
- intent and control collection
- scheduling of runtime-facing services and handlers
- run session lifecycle

Runtime must not own provider payload parsing, durable product artifacts, run config loading, mode-specific account bootstrap, broker execution, simulated fill policy, CLI output, or daemon product assembly.

Runtime may hold runtime-facing services such as output processing, request services, and mode-supplied execution/subscription handlers. It should not depend on concrete service-layer implementations such as venue clients, config assemblers, broker adapters, persistence repositories, or provider payload parsers. The service layer builds those concrete objects and hands stable runtime-facing services or handlers into the runtime.

## Target Data Flow

```text
Config/Surface/Service
  -> ModeAdapter
  -> RuntimeRunSpec
  -> RuntimeRunner
  -> RuntimeKernel
  -> RuntimeQueue
  -> RuntimeEngine
  -> RuntimeDataPipeline
  -> RuntimeProjectionRegistry
  -> ViewStore
  -> StrategyContext
  -> Strategy hook
  -> RuntimeOutputProcessor
  -> Runtime-facing services / ModeAdapter handlers
  -> RuntimeDataEnvelope follow-ups back into RuntimeQueue
```

The important constraint is that runtime only consumes and emits `RuntimeDataEnvelope` at its boundary. Backtest, paper, and live differ before and after the runtime kernel, not inside it.

## Target Package Shape

```text
kairospy/runtime/
  model/
    data.py
    mode.py
    result.py
    step.py
  source/
    protocol.py
  kernel/
    kernel.py
    engine.py
    state.py
    services.py
    output.py
    queue.py
    pipeline.py
    dispatcher.py
    context.py
    requests.py
  projection/
    base.py
    registry.py
    account/
      current.py
    execution/
      current.py
    intent/
      journal.py
    market/
      access.py
      projector.py
      publisher.py
      store.py
      views.py
    order/
      __init__.py
    reference/
      __init__.py
    risk/
      events.py
    system/
      runtime.py
      events.py
  run/
    spec.py
    runner.py
    line.py
    modes.py
    session.py
  control/
    daemon.py
```

### `runtime.model`

Owns stable runtime data structures:

- `RuntimeDataEnvelope`
- runtime payload wrappers for account, execution, and system events
- envelope summaries
- `RuntimeMode`
- `RunProfile`
- `StrategyRunResult`
- `StrategyCallbackRecord`
- `RuntimeStep`
- `RuntimeStepResult`
- `StrategyCallbackInvocation`

These types should be small value objects. They should not load config, read files, call exchanges, write journals, or instantiate mode engines.

### `runtime.source`

Owns source protocols and envelope-only sources:

- `EventSource`
- `AsyncEventSource`
- async close helpers

Runtime sources should yield `RuntimeDataEnvelope`. Row-to-domain normalization belongs outside the runtime source layer. For example, JSON rows becoming `MarketEvent(value=Bar(...))` or another standard market object should be assembled by market/data service code before runtime sees the event.

### `runtime.kernel`

Owns the core event loop:

- `RuntimeKernel`
- `RuntimeKernelSession`
- `RuntimeEngine`
- `RuntimeState`
- `RuntimeServices`
- `RuntimeOutputProcessor`
- `RuntimeQueue`
- runtime pipeline ingestion
- domain-to-hook dispatch
- `StrategyContext` creation
- strategy output collection
- subscription change detection
- strategy runtime request services, such as guarded quote requests during clock callbacks

The kernel package executes lifecycle and event ordering through `RuntimeEngine`. `RuntimeKernel` is the assembly shell around `RuntimeState`, `RuntimeServices`, and `RuntimeProjectionRegistry`. `RuntimeServices` is intentionally a runtime-facing service container, not a generic port/capability abstraction layer: it groups services that the runtime itself schedules, such as context creation, output handling, and guarded request providers. `RuntimeOutputProcessor` owns strategy outputs, intent handler follow-ups, and subscription changes. `RuntimeQueue` is the single synchronous queue for started, pre, source, and follow-up events. Follow-up envelopes returned by mode handlers go back through the same runtime loop; they are not side-channel view updates.

The kernel may call the projection group and publish runtime views. It should not know whether the run is backtest, paper, or live beyond the profile passed in through a run spec.

### `runtime.projection`

Owns runtime-scheduled projections:

- `RuntimeComponent`
- `RuntimeProjectionRegistry`
- `MarketProjection`
- `SystemProjectionAdapter`
- system strategy/data/dataflow/control view publication
- intent journal view publication
- market runtime state and `market.*` view publication
- account current projection component wiring
- execution current projection component wiring
- order projection namespace for future independent order read models
- reference projection namespace for future strategy-facing reference read models
- risk event projection wiring

Projection components consume envelopes and publish views. Domain-specific state machines stay in `core.*` or `service.domains.*`; runtime projections adapt those states into strategy-readable views. `RuntimeKernel` talks to `RuntimeProjectionRegistry`; it does not construct each `system.*`, `market.*`, `account.*`, `execution.*`, `intent.*`, or `risk.*` view payload inline. Projection domains are packaged consistently: `market/` owns market state, envelope consumption, view publication, strategy reads, and DTOs; `account/` exposes account current projection wiring; `execution/` exposes execution coordinator read-model wiring; `intent/` owns the intent journal read model currently published at the compatible key `system.intents`; `order/` is reserved for independent order read models when order state is injected separately from the execution coordinator; `reference/` is reserved for strategy-facing reference read models when reference catalogs become runtime-published views; `system/` owns runtime system views and system event projection; `risk/` owns runtime risk event projection until a full risk engine exists. Shared protocols live in `base.py`, and scheduling lives in `registry.py`.

Runtime projections publish views. Core domains may still expose pure state derivation, such as `core.account.AccountState`, but those objects are not projections and do not publish `ViewStore` entries. `AccountState` should not carry order journal state; runtime or service code can compose active orders beside it when needed. Core packages must not define `ViewSchema`/`on_event` runtime components; those live under `runtime.projection`.

### `runtime.run`

Owns run assembly primitives that are still mode-neutral:

- `RuntimeRunSpec`
- `RuntimeRunner`
- `RuntimeLine`
- event ordering rules
- sync and async run sessions

`RuntimeRunSpec` is the convergence point for mode-specific assembly. Mode engines and service config code should produce a spec; runtime should execute the spec.

Suggested shape:

```python
@dataclass(frozen=True, slots=True)
class RuntimeRunSpec:
    run_id: str
    profile: RunProfile
    strategy: Strategy
    source: EventSource
    state_config: RuntimeStateConfig
    service_config: RuntimeServiceConfig = RuntimeServiceConfig()
    projection_config: RuntimeProjectionConfig = RuntimeProjectionConfig()
    pre_events: tuple[RuntimeDataEnvelope, ...] = ()
    started_at: datetime | None = None
```

`RuntimeRunner` executes this shape synchronously. Mode engines should produce a spec and call `RuntimeRunner.run(...)`; streaming mode adapters use `RuntimeAsyncEnvelopeBridge` only as an outer I/O bridge from async event sources into the same synchronous kernel session. Account projections are ordinary runtime components assembled by the mode or service layer and injected through `components`; runtime does not construct account projections or return account-specific result fields. Runtime request providers are mode/service-supplied runtime services; any facts returned by a synchronous request should be emitted back into the runtime as `RuntimeDataEnvelope` follow-up events.

### `runtime.control`

Owns low-level run control primitives:

- daemon process identity
- daemon status
- daemon control plane
- execution context passed to daemon targets
- stop and heartbeat files

Control code should remain generic. It should not construct a `BacktestEngine`, `PaperEngine`, `LiveEngine`, exchange client, or run config. Those are service/mode responsibilities.

## Current File Classification

Current files can be classified as follows.

| Current file | Target layer | Note |
| --- | --- | --- |
| `runtime/model/data.py` | `runtime.model.data` | Owns envelope/payload/value DTOs and dataflow view DTOs. |
| `runtime/model/result.py` | `runtime.model.result` | Owns strategy callback/run result DTOs. |
| `runtime/model/step.py` | `runtime.model.step` | Owns one-step runtime DTOs used to make the lifecycle readable. |
| `runtime/kernel/pipeline.py` | `runtime.kernel.pipeline` | Owns run-scoped ingestion state and `system.dataflow` projection schema. |
| `runtime/kernel/kernel.py` | `runtime.kernel.kernel` | Owns `RuntimeKernel` and synchronous run session lifecycle. |
| `runtime/kernel/step.py` | removed | One-envelope processing lives in `runtime.kernel.engine`; no compatibility step processor remains. |
| `runtime/kernel/output.py` | `runtime.kernel.output` | Owns strategy output normalization, intent recording, mode handler calls, and subscription diffs. |
| `runtime/kernel/queue.py` | `runtime.kernel.queue` | Owns started/pre/source/follow-up event queue semantics. |
| `runtime/kernel/context.py` | `runtime.kernel.context` | Owns `StrategyContext` construction and runtime signal adaptation. |
| `runtime/kernel/dispatcher.py` | `runtime.kernel.dispatcher` | Owns runtime domain to strategy hook/phase mapping. |
| `runtime/run/spec.py` | `runtime.run.spec` | Owns `RuntimeRunSpec`, the mode-to-runtime handoff contract. |
| `runtime/run/runner.py` | `runtime.run.runner` | Owns `RuntimeRunner`, which executes a spec through `RuntimeKernel`. |
| `runtime/run/session.py` | `runtime.run.session` | Owns `RuntimeRunResult` and `RuntimeRunSession`. |
| `runtime/run/bridge.py` | `runtime.run.bridge` | Owns async source-to-envelope bridging into a synchronous runtime session. |
| `runtime/run/line.py` | `runtime.run` | Owns `RuntimeLine` and event ordering. |
| `runtime/model/mode.py` | `runtime.model.mode` | Owns `RuntimeMode` and run profile declarations. |
| `runtime/projection/base.py` | `runtime.projection.base` | Owns generic runtime projection and component protocols. |
| `runtime/projection/registry.py` | `runtime.projection.registry` | Owns unified system/market/component projection scheduling. |
| `runtime/projection/account/current.py` | `runtime.projection.account` | Exposes account current projection wiring through the runtime projection namespace. |
| `runtime/projection/execution/current.py` | `runtime.projection.execution` | Exposes execution coordinator read-model wiring through the runtime projection namespace. |
| `runtime/projection/system/runtime.py` | `runtime.projection.system` | Owns `system.*` runtime view publication for strategy/data/dataflow/control. |
| `runtime/projection/intent/journal.py` | `runtime.projection.intent` | Owns intent journal read-model publication. The compatibility view key remains `system.intents`. |
| `runtime/projection/order/__init__.py` | `runtime.projection.order` | Owns the order projection namespace. Active order read models currently come through `execution.current` because execution owns the coordinator. |
| `runtime/projection/reference/__init__.py` | `runtime.projection.reference` | Owns the reference projection namespace. Runtime-published reference views should be added here when needed. |
| `runtime/projection/system/events.py` | `runtime.projection.system` | Owns `system.events` projection. |
| `runtime/projection/risk/events.py` | `runtime.projection.risk` | Owns `risk.events` projection for system-domain `risk.*` envelopes. |
| `runtime/projection/market/store.py` | `runtime.projection.market` | Owns market runtime state and typed update application. |
| `runtime/projection/market/projector.py` | `runtime.projection.market` | Owns market envelope consumption through `MarketProjection`. |
| `runtime/projection/market/publisher.py` | `runtime.projection.market` | Owns `market.*` view publication. |
| `runtime/projection/market/access.py` | `runtime.projection.market` | Owns strategy-side market read facade. |
| `runtime/projection/market/views.py` | `runtime.projection.market` | Owns market view DTOs and summaries. |
| `runtime/kernel/requests.py` | `runtime.kernel.requests` | Owns strategy runtime request facades and request provider protocols; projection does not own external request providers, and request results re-enter runtime as envelopes. |
| `runtime/source/protocol.py` | `runtime.source` | Owns only source protocols and async close helper. Row-to-`MarketEvent` normalization lives in `service.domains.market.sources`. |
| `runtime/run/modes.py` | `runtime.run` | Owns mode-neutral runtime line start-event wrapping. Account baseline bootstrap lives in `service.domains.account.baseline`. |
| `runtime/control/daemon.py` | `runtime.control` | Keep generic control plane; keep mode target construction outside. |
| `runtime/account_journal.py` | removed | Implementation lives in `service.operations.run.journal`; runtime no longer owns this path. |
| `runtime/accounts.py` | removed | Implementation lives in `service.operations.run.accounts`. |

## Mode Boundary

Modes should not be alternative runtimes. They are adapters around one runtime kernel.

```text
modes.backtest
  -> builds historical source
  -> builds simulated account bootstrap events
  -> installs simulated intent handler
  -> receives RuntimeRunResult
  -> computes BacktestResult and metrics

modes.paper
  -> builds replay or streaming source
  -> builds simulated account bootstrap events
  -> installs simulated intent handler
  -> uses paper account environment
  -> receives RuntimeRunResult

modes.live
  -> bootstraps venue account through service account gateway
  -> collects private account events
  -> installs live execution intent handler
  -> receives RuntimeRunResult
  -> persists live state through live service code
```

Paper should share simulated execution/accounting components with backtest, but it should not mutate or wrap a `BacktestEngine` instance to become paper. The shared part should be an adapter or service object with explicit inputs; it should live outside a backtest-only engine module so paper and backtest are peers.

## Allowed Dependencies

Runtime may import:

- `kairospy.application.context`
- `kairospy.application.strategy`
- `kairospy.core.*`
- standard library

Runtime must not import:

- `kairospy.config`
- `kairospy.application.mode.*`
- `kairospy.infrastructure.integrations.*`
- `kairospy.surface.*`

Runtime should avoid importing:

- `kairospy.infrastructure.data` concrete storage
- service operation modules

If runtime needs a concept from these layers, the concept should be passed in through a protocol, source, handler, component, or run spec.

## Structure Rules

- Use `Runtime` only for event-loop and run-kernel concerns.
- Use `RunProfile` for mode declarations such as backtest, paper, and live.
- Use `ModeAdapter` for code that turns mode-specific I/O and execution into runtime specs and handlers.
- Use `Projection` for state derived from envelopes.
- Use `View` for strategy-readable state snapshots.
- Use `Source` only for objects that yield `RuntimeDataEnvelope`.
- Use `Journal` for artifact persistence outside runtime.
- Use `Configured*` only at service boundaries, not inside runtime.

## Enforcement Plan

The runtime shape is guarded by architecture tests. Keep these invariants true:

- Runtime code must not import config, modes, integrations, surface, or service operation modules.
- Product and mode code should hand `RuntimeRunSpec` to `RuntimeRunner.run(...)`. Async streaming adapters may use `RuntimeAsyncEnvelopeBridge`, but `RuntimeKernel` remains synchronous and only sees one envelope at a time through `RuntimeKernelSession.process(...)`.
- `RuntimeRunner` constructs `RuntimeKernel` and does not assemble account projections or mode-specific result fields.
- `RuntimeKernel` assembles `RuntimeState`, runtime-facing `RuntimeServices`, `RuntimeEngine`, `RuntimeQueue`, and `RuntimeProjectionRegistry`; it does not publish domain views directly.
- Intent handler follow-up envelopes go back through the runtime event loop, so account/execution/system callbacks and projections see the same path as source events.
- Backtest and paper share simulated execution/accounting through `service.domains.execution.SimulatedRunAdapter`; paper must not import `kairospy.application.mode.backtest.engine`.

## Design Check

A new runtime feature belongs in `kairospy.application.runtime` only if all answers are yes:

- Does it apply identically to backtest, paper, and live?
- Does it consume or produce `RuntimeDataEnvelope`, `ViewStore`, strategy callbacks, or run state?
- Can it work without loading a run config?
- Can it work without provider-specific payloads or exchange clients?
- Can it work without writing product artifacts to disk?

If any answer is no, place it in `service`, `modes`, `data`, `integrations`, or `surface` and inject the result into runtime.
