# Project Architecture and Agent Rules

This repository separates business modules, system composition, platform
capabilities, and shared primitives. The architecture baseline, ownership map,
and Agent-specific change rules are maintained in this file and apply before
adding, moving, or deleting code.

## Repository layout

```text
crates/
  modules/          independently owned business modules
    <module>/       the module's main crate
      contract/     optional, independently depend-able process contract crate
      src/          application, composition, domain, services, and binaries
  system/           cross-business process composition and launch coordination
  platform/         business-neutral infrastructure capabilities
  primitives/       grouped, infrastructure-free shared business vocabulary
```

The main crate lives directly at `crates/modules/<module>`; do not add a
`service`, `runtime`, `app`, or standalone `domain` crate merely to mirror an
internal source layer. A module contract is a separate Cargo package so a
caller can depend on `crates/modules/<module>/contract` without compiling or
importing the main module crate. Directory nesting never implies a Cargo
dependency.

System crates live under `crates/system/<capability>` and may assemble owner
contracts with platform resources, but must not bypass a contract to invoke a
business main package. Business modules may depend on a System runtime only for
process lifecycle and typed resource access; business behavior remains in the
owner module.

Platform crates live under `crates/platform/<capability>`. They must remain
business-neutral: do not put business state, module contract inventories, or
module-owned vocabulary in platform crates. `crates/primitives` is a
shared semantic kernel, not a generic common-types or utilities bucket. It may
contain business identities, exact values, units, and closed vocabulary whose
meaning and invariants are stable across an owner's domain/contract boundary
or genuinely shared by multiple modules.
Organize business primitives by their governing business vocabulary, such as
account, execution, market, reference, risk, and integration. Keep genuinely
cross-cutting value mechanics such as decimal and time in their own groups.
These groups clarify ownership; they are not miniature domain modules.
Expose business primitives through those owner namespaces and prefer imports
such as `kairos_primitives::execution::OrderId` over an undifferentiated crate
root. Do not add new wildcard root re-exports that erase the grouping.

A primitive may still have a business governance owner. Ownership determines
who may change its meaning; placement in primitives allows contracts and
domains to use the same small value without contract-to-contract dependency
cycles or duplicate canonical definitions. Commands, queries, events,
snapshots, lifecycle models, policies, and workflows never become primitives.

## Standard module layout

Every main module crate should converge on these first-level directories:

```text
src/
  bin/             compiled server and CLI entry points
  composition/     concrete integrations, stores, publishers, and mode setup
  application/     main-package use-case facade and optional process facade
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

The boundary is defined by Cargo packages. Within one module's main package,
its binary targets, composition, process adapters, and package tests enter
business behavior through that package's application. A different business
package under `crates/modules` must not import the main package or its
application; it enters exclusively through the owner's contract package.

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

Application must not import composition. Composition may construct and adapt
its own package's application. It must not use composition as a shortcut to
call another business package's application; cross-business access uses the
owner's contract.

### `contract/`: cross-business facade

The optional `contract/` directory is a separate Cargo package and is the only
public business boundary for other packages under `crates/modules`. It owns
the commands, queries, events, snapshots, capability clients, and wire
adapters that another business package may use. It composes those module-owned
messages from shared primitives where the field has project-wide semantics;
the use of a primitive does not transfer ownership of the message itself.

A contract must not depend on its owner's main package or expose domain
entities, service instances, provider payloads, or persistence records. The
owner's server or transport adapter maps contract-owned input into its
application API and maps application/domain results into contract-owned
output. Contract crates may use primitives and platform protocol/transport
capabilities without transferring business ownership to those lower layers.
Shared process-boundary mechanics belong to `kairos_protocol`: runtime
metadata, event/view encoding context, frame/version handling, and protocol
errors. Module contracts compose that common context with their own commands,
queries, events, snapshots, and business keys; they must not duplicate a
second metadata builder or move business DTOs into protocol.

Contract-to-contract dependencies are not the default. Shared identity and
value atoms should normally come from primitives, allowing contracts to use a
common Rust type without depending on one another. Add a contract dependency
only when a current contract genuinely consumes the other module's complete
message or capability and the dependency direction is stable and acyclic.

### `application/`: main-package facade

Application is the main package's internal use-case boundary. It exposes
business-oriented commands, queries, results, and errors to that package's
binary targets, composition, process adapters, and package tests. It is not a
cross-business API: another package under `crates/modules` must use the
owner's contract instead. Application must not expose SDK clients, raw vendor
payloads, persistence records, composition records, or service instances.

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
contract, protocols, infrastructure, SDK, or external implementation. A
consumer application may adapt facts obtained through another module's
contract into its own domain vocabulary; the domain itself remains unaware of
the foreign package.

## Dependency and abstraction rules

- `primitives` is the common dependency for small, validated business values
  whose semantics are shared across modules. Contracts and main packages may
  depend on it; primitives must not depend on a business or platform crate.
- Admission to primitives requires current use across an owner domain/contract
  boundary or by multiple modules with the same meaning and invariants, an
  infrastructure-free representation, and a stable validation path. A purely
  internal type stays in its domain. Similar field names or identical Rust
  representations are not sufficient.
- Once a primitive exists, contract, application, and domain models use that
  type directly for the same meaning. Do not downgrade `AccountId`,
  `MarketId`, `UnixNanos`, `Price`, or another established value to
  `String`/integer and reconstruct it in the next layer merely to preserve a
  JSON, FlatBuffers, SQL, CLI, or provider representation. Serde-transparent
  primitives and explicit adapters preserve the wire shape.
- Raw representations may remain in generated wire accessors, provider SDK
  DTOs, CLI/config input, and private persistence rows. Convert them once at
  the boundary with a fallible constructor; do not let raw semantic values
  flow through contract, application, or domain models.
- Do not put DTOs, orchestration, lifecycle state, persistence/wire records,
  SDK types, generic helpers, or convenience utilities in primitives.
- `application/` is the use-case entry point for targets and tests belonging
  to the same main Cargo package, including its server, CLI, composition, and
  reusable process facade.
- `contract/` is the only public business entry point for a different package
  under `crates/modules`.
- A business main package must not depend on another business main package.
  It may depend on the other module's independently depend-able contract.
- `application/`, `domain/`, `services/`, composition records, and persistence
  records are never imports across business package boundaries.
- Direct dependency inside one main package is the default. Application may
  directly depend on its package's Domain, Actor, and concrete private
  services. Cross-business calls use the owner contract directly; do not
  mirror it with an application-owned port or call the owner application.
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
  Application or Domain and call the same-package concrete service or the
  foreign owner contract directly. Do not introduce a `ports/` layer as an
  architectural default.
- `protocol` is optional, not a mandatory layer.
- Prefer an existing `kairos-integration` application capability directly when
  the business module is intentionally coupled to integration.
- Add a protocol only when the owning lower-level module has an established,
  minimal capability with current production implementations and callers.
- Do not duplicate an integration API, rename vendor concepts without a
  business reason, or add a protocol only for uniform dependency injection.
- Concrete connectors, stores, publishers, and mode-specific implementations
  are selected in composition or test fixtures.
- Application APIs use package-owned business request/result types and do not
  expose vendor payloads or persistence records. Contract APIs use
  contract-owned request/result/event/snapshot types and do not re-export the
  owner's application or domain models.
- Cross-package and cross-process contract DTOs use fixed-width numeric types
  or semantic wrappers; do not expose `usize` or `isize`. Counts, revisions,
  sequences, versions, timestamps, and durations are distinct meanings and
  must not be treated as interchangeable merely because they share an integer
  representation.
- `serde_json::Value` is allowed only for an explicitly named extension,
  diagnostic details, configuration, or provider-specific boundary. It must
  not carry a core command, query, event, snapshot, intent, admission record,
  or other business payload when a typed contract can be defined. When a
  closed request enum replaces a generic JSON envelope, migrate current
  callers and remove the duplicate facade.
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
- Cross-business orchestration belongs in the consuming package's application
  and uses owner contracts. System composition may construct processes and
  connect contract clients, transports, and resources, but must not bypass a
  contract to invoke another business package's application directly.

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
3. Decide whether the caller belongs to the same main Cargo package or a
   different business package.
4. For a cross-package capability, define or verify the owner contract first;
   for same-package invocation, define or verify the application API.
5. Map contract input/output explicitly at the owner process boundary; do not
   expose application or domain types through the contract.
6. Assign mutable state to exactly one Actor.
7. Select concrete implementations in composition.
8. Put reusable process/control behavior in `application/process.rs`; keep
   transport-only details private to the binary.
9. Keep binaries limited to input adaptation, composition, and invocation.

## Verification before handoff

### Python changes

After changing Python code, always run the full-project type check:

```text
make python-type-check
```

Before handing off a Python-affecting task, run that command again in addition
to focused tests for the changed behavior. A Python-affecting task is not
complete while Pyright reports an error or warning. Fix dynamic JSON, TOML,
provider, and persistence values by validating and converting them once at
their owning boundary. Do not introduce `Any`, an unchecked `cast(...)`, or
`# type: ignore` solely to silence the type checker. If an unrelated
pre-existing failure blocks the gate, report the exact failure and still run
Pyright on the affected package; do not claim the gate passed.

### Workbench TUI development

The Workbench is a keyboard-first Textual command line. Its normal layout is a
`RichLog` output region above one guided `Input`; do not reintroduce a button
menu, a chat transcript, a second App, or a UI-specific business facade.

Before adding a Workbench feature or result presentation, classify it against
the existing product vocabulary. First identify the business owner, user
question, one of the six result templates, and any of the five state overlays.
Then reuse an existing production flow, renderer, or business-neutral
presentation primitive. If the fit is incomplete, make the smallest change to
the owning flow or shared primitive that preserves those semantics. Propose a
new pattern only when the existing templates and overlays cannot truthfully
express the current user question; visual difference or a hypothetical future
caller is not sufficient.

A change that adds a Workbench pattern must state the concrete current problem
and caller, why reuse or adaptation is insufficient, the smallest new semantic
addition, how it composes with Activity and Live Control boundaries, and which
behavior, copy, accessibility, and snapshot tests prove it. Do not introduce a
generic renderer, registry, or compatibility facade merely to make unlike
business results look structurally uniform.

For UI work, create the ignored, credential-free fixture with:

```text
uv run python scripts/maintenance/create_workbench_fixture.py
```

Always launch Agent-driven sessions with that fixture and both safety flags:

```text
TEXTUAL=debug,devtools KAIROS_TEXTUAL_DEV=1 uv run kairos interactive \
  --workspace .agent-work/textual-agent-workflow/fixture/.kairos \
  --dry-run --no-exec
```

Run `uv run textual console` in another terminal when message and CSS-error
diagnostics are needed; CSS hot reload itself is controlled by
`KAIROS_TEXTUAL_DEV`.

Install the pinned real-terminal driver with
`sh scripts/maintenance/install_tui_test.sh`. When Codex drives it, keep a
persistent PTY shell alive, run the CLI from
`.agent-work/textual-agent-workflow/bin/tui-test`, use one named session, wait
for `idle` instead of sleeping, and close the session on success or failure.
Inspect `text --json` and `cells ... --json` before requesting an SVG. A
developer can attach to the same named session with `monitor`.

Run the fast UI layers with:

```text
uv run pytest -q tests/workbench/test_app_*.py
uv run pytest -q tests/workbench/test_snapshots.py
uv run pytest -q tests/workbench/test_binary.py
```

Run focused tests for changed modules and then the repository checks relevant
to the change:

```text
cargo test --workspace
uv run pytest -q
make rust-fmt-check
git diff --check
python3 scripts/check/check_crate_layout.py
python3 scripts/check/check_workspace_dependencies.py
python3 scripts/check/check_documentation.py
make docs-check
```

If an unrelated pre-existing failure blocks a full-repository check, report
the exact failure and still run the narrowest meaningful checks.
