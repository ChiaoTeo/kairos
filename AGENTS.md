# Project Architecture and Agent Rules

This repository separates business modules, platform capabilities, and shared
primitives. The architecture baseline, ownership map, and Agent-specific
change rules are maintained in this file and apply before adding, moving, or
deleting code.

## Repository layout

```text
crates/
  modules/          independently owned business modules
    <module>/       the module's main crate
      contract/     optional, independently depend-able process contract crate
      src/          application, composition, domain, services, and binaries
  platform/         infrastructure and system capabilities
  primitives/       infrastructure-free values genuinely shared by modules
```

The main crate lives directly at `crates/modules/<module>`; do not add a
`service`, `runtime`, `app`, or standalone `domain` crate merely to mirror an
internal source layer. A module contract is a separate Cargo package so a
caller can depend on `crates/modules/<module>/contract` without compiling or
importing the main module crate. Directory nesting never implies a Cargo
dependency.

Platform crates live under `crates/platform/<capability>`. Do not put business
state or module-owned vocabulary in platform crates. `crates/primitives` is
not a generic common-types bucket: add a type only when its meaning and
invariants are genuinely shared by multiple modules.

## Standard module layout

Every main module crate should converge on these first-level directories:

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
- Direct dependency is the default. Application may directly depend on its
  module's Domain, Actor, and concrete private services, and may directly use
  another module's application API or independently depend-able contract.
- Do not create an application-owned dependency-inversion trait merely to hide
  a concrete service, make dependency injection uniform, provide test doubles,
  or anticipate future implementations. A wrapper, queued worker, socket
  decorator, and test fake around the same implementation do not constitute
  multiple capability implementations.
- A trait dependency is allowed only when an integrated lower-level module or
  platform capability already owns a stable polymorphic boundary with current
  production implementations. Depend on that owner-defined boundary directly;
  do not mirror it with a second application `port`, `capability`, `gateway`,
  or `protocol` trait.
- If no such lower-level abstraction exists, keep the business rule in
  Application or Domain and call the concrete service/application/contract
  directly. Do not introduce a `ports/` layer as an architectural default.
- `protocol` is optional, not a mandatory layer.
- Prefer an existing `kairos-integration` application capability directly when
  the business module is intentionally coupled to integration.
- Add a protocol only when the owning lower-level module has an established,
  minimal capability with current production implementations and callers.
- Do not duplicate an integration API, rename vendor concepts without a
  business reason, or add a protocol only for uniform dependency injection.
- Concrete connectors, stores, publishers, and mode-specific implementations
  are selected in composition or test fixtures.
- Application APIs use business request/result types and do not expose vendor
  payloads or persistence records.
- Cross-process business event and snapshot publishers must map application or
  domain models directly into contract-owned types and encode those types with
  the declared wire format (normally FlatBuffers). Do not use
  `serde_json::to_value`/`from_value`, `serde_json::Value`, or an equivalent
  serialize/deserialize round trip as a typed model adapter. JSON remains
  acceptable only at an explicit configuration, control, persistence, or
  diagnostic boundary whose contract is intentionally JSON.
- Do not add a manager, coordinator, processor, callback layer, registry, or
  compatibility facade before checking whether an existing Domain,
  Application, Actor, Monitor, or composition boundary already owns it.

## Documentation rules

Routine development tasks must not create a proposal, design document, or
generated report under `docs/`. The task discussion, change description, code,
and tests are the normal record of implementation work.

Agents may store temporary analysis, plans, generated reports, and other
working material under `.agent-work/<task>/`. The entire directory is ignored
by Git, is not project documentation, and must never be referenced by committed
files. Do not store credentials, provider payloads containing secrets, or the
only copy of evidence required to maintain the project there.

Committed documentation is organized by its durable audience and owner:

- `docs/guides/` explains user workflows;
- `docs/architecture/` describes current cross-module architecture;
- `docs/integrations/` records current provider coverage, upstream provenance,
  certification evidence, and license obligations;
- `docs/decisions/` records accepted, long-lived architectural decisions;
- `schemas/` owns machine-readable wire contracts and protocol semantics;
- an owning crate's `README.md` owns module-specific boundaries.

Do not create `docs/proposal/` or a generic `docs/reference/` directory. An
unresolved design stays in the task, an issue, or `.agent-work/`. Once a
significant decision is made, write a concise Decision containing its context,
choice, and consequences; do not preserve the full implementation plan. The
capitalized name `Reference` is reserved for the Reference business module.

Generated documentation is never committed. Local API pages and similar
artifacts belong under `target/docs/`; CI may publish them as disposable build
artifacts. Code and schemas remain the source of truth, and committed prose
must link to them rather than copy generated inventories.

## Cargo dependency rules

The root `Cargo.toml` is the single source of truth for workspace package
metadata, direct dependency versions, dependency sources, and internal crate
paths.

- Every workspace member inherits `version`, `edition`, and `license` from
  `[workspace.package]`.
- Every direct dependency, development dependency, build dependency, and
  target-specific dependency of a workspace member must be declared in
  `[workspace.dependencies]` and referenced with `workspace = true`.
- Every workspace member package has a path entry in
  `[workspace.dependencies]`. Member manifests must not repeat relative paths
  to other workspace crates.
- The root declaration owns versions, sources, disabled default features, and
  features that are required by every caller. A member manifest owns only its
  additional feature selection and whether a dependency is optional.
- Do not enable a broad feature set at the workspace level for the convenience
  of one caller. In particular, blocking runtimes, database migrations, macros,
  and provider-specific transport features stay with the member that uses
  them.
- A workspace dependency entry does not authorize a dependency edge. Each
  member must still list every dependency it uses, so its architectural
  boundary remains visible in its own manifest.
- Adding or changing a dependency requires running
  `python3 scripts/check/check_workspace_dependencies.py` and committing the
  resulting `Cargo.lock` change when dependency resolution changes.

## Ownership rules

Before changing code, identify the business owner, mutable state owner,
command/event/query type, and target layer.

For the current business modules:

- Account owns balances, positions, equity, freshness, intents, and
  account-side order facts.
- Live Account facts have one authoritative ingress: Account-owned Integration
  account snapshot/event capabilities. Execution or another business module
  must not push a duplicate live order, fill, balance, or position observation
  into Account.
- Simulation-only Account mutations must use an explicitly named simulation
  command, be rejected by live Account processes, and remain idempotent. Do not
  generalize that exception into an Account-facts port or a production event
  ingestion API.
- Execution owns the exchange-facing order lifecycle and execution audit.
- Risk owns budgets and reservations.
- Market owns observations, order books, subscriptions, and freshness.
- Reference owns the reference catalog and lifecycle facts.
- Integration owns provider authentication and normalized external facts.
- Workspace/System owns paths, process lifecycle, instance resources, and
  launch coordination.
- Cross-business orchestration belongs in application or system composition.

## Engineering quality and anti-overdesign rules

Prefer the smallest change that solves the current problem. Engineering
quality means clearer ownership, safer boundaries, fewer invalid states, and
better evidence—not a larger number of layers or abstractions.

- Do not add a manager, coordinator, registry, protocol, port, capability
  trait, or compatibility facade unless its owning lower-level module already
  has a current caller and multiple real production implementations. A
  hypothetical second implementation or a test fake is not sufficient.
- Prefer an existing owner or boundary over introducing a new layer. Do not
  optimize for uniform file layouts when responsibility is already clear.
- Introduce shared domain types only when the semantic meaning is genuinely
  shared. Do not create universal types merely to remove every primitive.
- Keep primitive representations at wire, persistence, and integration
  boundaries. Use explicit conversions into domain types rather than implicit
  primitive conversions.
- Require benchmark or profiling evidence before introducing performance
  abstractions such as caching, batching, zero-copy paths, or generalized
  dispatch.
- Treat behavior tests, boundary tests, architecture checks, and error
  handling as part of the change, not as follow-up work.

Before adding a non-trivial abstraction, answer these questions in the change
description or design note:

1. What concrete problem does it solve now?
2. Who is its current caller?
3. Which existing owner or boundary is insufficient?
4. What is the simplest implementation that preserves the boundary?
5. What test or measurement will demonstrate that the change is useful?

## Change workflow

1. Apply the architecture and ownership rules in this file.
2. Identify ownership and verify the domain rule.
3. Define or verify the application request/result API.
4. Assign mutable state to exactly one Actor.
5. Select concrete implementations in composition.
6. Put reusable process/control behavior in `application/process.rs`; keep
   transport-only details private to the binary.
7. Keep binaries limited to input adaptation, composition, and invocation.

## Verification before handoff

Run focused tests for changed modules and then the repository checks relevant
to the change:

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
python3 scripts/check/check_workspace_dependencies.py
python3 scripts/check/check_documentation.py
make docs-check
```

If an unrelated pre-existing failure blocks a full-repository check, report
the exact failure and still run the narrowest meaningful checks.
