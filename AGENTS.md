# Project Architecture and Agent Rules

This repository uses one standard shape for every business module. The
architecture baseline and ownership map are maintained in
[`docs/module-boundaries.md`](docs/module-boundaries.md); read that file in
full before adding, moving, or deleting code.

## Standard module layout

Every business crate should converge on these first-level directories:

```text
src/
  bin/             compiled server and CLI entry points
  composition/     concrete integrations, stores, publishers, and mode setup
  application/     public use-case facade and optional process facade
  services/        private actors, persistence, adapters, and publishers
  domain/          entities, value objects, and business invariants
```

`services/` is intentionally plural and is the standard name. A module may
have additional files only when their ownership is clear; do not create a new
top-level layer merely to avoid assigning an existing responsibility.

The normal startup and invocation flow is:

```text
bin -> composition -> application -> services
                         \\-> domain
```

This is a call/construction flow, not a rule that application may import
composition. `bin` invokes composition. Composition selects concrete
implementations and builds the application. Application orchestrates use
cases through private services. Domain is a sibling business core used by
application and services, and must remain free of infrastructure concerns.

## Layer responsibilities

### `bin/`: compiled entry points

`bin/` contains server and CLI binaries. It may:

- parse command-line arguments and environment input;
- resolve workspace paths and instance resources;
- invoke composition;
- configure and run the application/process facade.

It must not define reusable business behavior, duplicate application use
cases, or become a second facade. Explicit Cargo `[[bin]]` targets should
point into `src/bin/`.

### `composition/`: concrete assembly

Composition owns concrete choices and wiring:

- integration/provider clients;
- persistence and publisher implementations;
- mode selection such as paper, simulated, or live;
- mapping external integration facts into module-owned business facts;
- construction of the application and its services.

Application must not import composition. Cross-module callers must not import
another module's services or private files; they enter through that module's
application API.

### `application/`: public facade

Application is the module's public use-case boundary. It exposes
business-oriented commands, queries, results, and errors. It must not expose
SDK clients, raw vendor payloads, persistence records, composition records, or
service instances.

An optional `application/process.rs` is the module runtime facade when a
reusable process owns an application instance and exposes control, lifecycle,
health, event draining, or shutdown behavior. `ExecutionProcess` is the model
case: it can live under application because it is a runtime facade around
`ExecutionApplication`, not because it is a business entity. It must not own
business state or select concrete integrations.

If process code is entirely transport-specific and has no reusable module
runtime contract, it may remain private to the corresponding binary. Do not
move such code into application just to satisfy a folder rule.

### `services/`: private implementation

`services/` contains internal actors, data loops, persistence, publication,
integration adapters, and process internals. A service is not public merely
because a CLI, server, actor, or test needs it. The service layer must not
become an alternative application facade.

Mutable business state has exactly one owner, normally an Actor. The Actor
owns its data loop and emits business events. A process facade may drive the
Actor through application APIs but must not become a second state owner.

### `domain/`: business core

Domain contains entities, value objects, validation, and business invariants.
It must not depend on another business module's application, services,
protocols, infrastructure, SDK, or external implementation. Cross-module
collaboration belongs in application orchestration or composition.

## Dependency and abstraction rules

- `application/` is the only public entry point for other modules, servers,
  CLIs, scheduled jobs, and external test fixtures.
- `services/` and other private files are never cross-module imports.
- `protocol` is optional, not a mandatory layer.
- Prefer an existing `kairos-integration` application capability directly when
  the business module is intentionally coupled to integration.
- Add a protocol only for a genuinely module-owned, minimal capability where
  multiple implementations or a clear isolation boundary justify it.
- Do not duplicate an integration API, rename vendor concepts without a
  business reason, or add a protocol only for uniform dependency injection.
- Concrete connectors, stores, publishers, and mode-specific implementations
  are selected in composition or test fixtures.
- Application APIs use business request/result types and do not expose vendor
  payloads or persistence records.
- Do not add a manager, coordinator, processor, callback layer, registry, or
  compatibility facade before checking whether an existing Domain,
  Application, Actor, Monitor, or composition boundary already owns it.

## Ownership rules

Before changing code, identify the business owner, mutable state owner,
command/event/query type, and target layer.

For the current business modules:

- Account owns balances, positions, equity, freshness, intents, and
  account-side order facts.
- Execution owns the exchange-facing order lifecycle and execution audit.
- Risk owns budgets and reservations.
- Market owns observations, order books, subscriptions, and freshness.
- Reference owns the reference catalog and lifecycle facts.
- Integration owns provider authentication and normalized external facts.
- Workspace/System owns paths, process lifecycle, instance resources, and
  launch coordination.
- Cross-business orchestration belongs in application or system composition.

## Change workflow

1. Read `docs/module-boundaries.md` completely.
2. Identify ownership and verify the domain rule.
3. Define or verify the application request/result API.
4. Assign mutable state to exactly one Actor.
5. Select concrete implementations in composition.
6. Put reusable process/control behavior in `application/process.rs`; keep
   transport-only details private to the binary.
7. Keep binaries limited to input adaptation, composition, and invocation.
8. Delete obsolete concepts after migration; do not preserve an abstraction
   without a current caller or boundary.

## Verification before handoff

Run focused tests for changed modules and then the repository checks relevant
to the change:

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

Also run static searches for cross-module imports from `services/` or private
files, vendor payloads crossing application boundaries, duplicate state
owners, unnecessary protocol mirrors, and generic orchestration layers.

If an unrelated pre-existing failure blocks a full-repository check, report
the exact failure and still run the narrowest meaningful checks.
