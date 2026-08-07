# Module boundaries

This document is the repository baseline for the DDD convergence work. A
module owns its business state and exposes use cases through `application/`.
Implementation remains private in `services/`; concrete connectors and stores
are selected by composition.

## Rules

- Every business module uses the same first-level layout: `bin/`,
  `composition/`, `application/`, `services/`, and `domain/`.
- Code outside a module enters through that module's `application/` API. It
  must not import the module's `services/` or private files.
- `application/` is the module's public facade. It exposes business-oriented
  commands, queries, results, and errors; it does not expose service instances
  or composition records.
- `services/` is internal implementation. A service is not made public merely
  because a CLI, server, actor, or test needs it. Process/control adapters do
  not belong here when they are the module's externally callable runtime
  facade.
- `protocol` is optional, not a mandatory layer. Prefer a direct dependency on
  an existing integration application capability when the business module is
  intentionally coupled to that integration. Add a protocol only when the
  consuming module owns a genuinely minimal capability that must be supplied
  by more than one implementation or must be isolated for a clear boundary.
- A protocol must not duplicate an integration API, rename vendor concepts
  without a business reason, or exist only to make dependency injection look
  uniform. Its implementation remains private.
- Domain code must not depend on another business module. Cross-module
  collaboration belongs in application orchestration or system composition.
- Application APIs use business request/result types. They do not expose SDK
  clients, vendor payloads, persistence records, or service instances.
- Concrete integration clients, stores, publishers, and mode-specific
  implementations are selected in composition or test fixtures.
- An optional `application/process.rs` owns the module runtime facade when it
  holds the application, accepts control requests, drains application events,
  or coordinates process shutdown. It may orchestrate application use cases,
  but it must not contain provider selection or concrete connector creation.
- `bin/` and explicitly configured `[[bin]]` targets are compiled process/CLI
  entry points only. They parse input, invoke composition, and run application
  APIs; they do not define reusable business behavior.
- Business `*-cli` binaries are one-shot application entry points. They do not
  create Unix socket clients or expose a server-control subcommand. Runtime
  process control and cross-process business access belong to the System
  boundary; `kairospy/application/system` owns the generic Unix REST transport
  and typed Account/Execution/Market/Risk/Reference clients.
- `kairos-workspace` and `kairos-integration` are infrastructure modules and
  must not depend on business modules.

## Ownership

| Responsibility | Owner |
| --- | --- |
| balances, positions, equity, freshness | Account application/domain |
| account intent and account-side order facts | Account application/domain |
| external order lifecycle and execution audit | Execution application/domain |
| risk reservations | Risk application/domain |
| market and reference state | Market/Reference application/domain |
| workspace paths, process lifecycle, instance resources | Workspace/System |
| provider authentication and normalized external facts | Integration |
| cross-business-module orchestration | System/application composition |

Account configuration and credential bindings are Account use cases and are
managed by `kairos-account-cli`. Secret values are resolved at composition time
and are not exposed as public application payloads. Launch leases are runtime
coordination state owned by Workspace/System; they are not part of Account
state.

## Account layering

```text
bin
    -> composition
    -> application use-case/process facade
    -> services
    -> domain
    -> integration/workspace implementations
```

`AccountApplication` is the public use-case facade. An optional
`AccountProcess` is the application runtime facade that holds it and exposes
control/lifecycle behavior. The Actor is the sole owner of mutable account
state. The process owns lifecycle, control transport, health, and snapshot
publication; it must not become a second account-state owner. Account
integration adapters should consume the existing Integration application API
directly; the account module should not create a second protocol hierarchy
that mirrors Integration.

## Standard business-module shape

```text
<business-module>/
  bin/                 thin compiled server/CLI entry points
  composition/         concrete integration/store/mode selection
  application/         public use-case/process facade
  services/             private Actor, persistence, adapters, publishers
  domain/               entities, value objects, invariants
```

The process facade is not a second business-state owner. It delegates
business behavior to `Application`, while the binary only parses arguments,
builds composition, and runs the process facade. If a process implementation
is entirely transport-specific and has no reusable module runtime contract,
it may remain private to the corresponding binary instead of becoming a new
application API.

## Current migration decisions

- The redundant `application/account/` nesting has been removed. Account use
  cases now live directly under `application/`.
- `application/protocol.rs` is a migration seam, not a design requirement. The
  next account refactor should remove the traits that only mirror Integration
  connections and keep only true account-owned boundaries, if any.
- `src/bin/` contains binary composition roots. They are not part of the
  reusable Account application facade.
- `kairospy` separates direct one-shot CLI invocation from instance control:
  business CLI facades invoke direct application commands, while
  `ComponentProcessApplication` starts processes and returns a typed System
  client for an instance-scoped socket.

## Verification

Changes should be checked with:

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

Static review must also check for cross-module imports from `services/`, vendor
payloads crossing application boundaries, duplicate state owners, and new
generic managers/coordinators that bypass an existing Actor or composition
boundary.
