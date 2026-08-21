# Conflux JSON-RPC control boundary

Document type: current architecture guidance.

This document defines the target shape for retiring the old REST-style
Conflux control design across business modules. Reference is the first closed
module on the new path and should be used as the implementation model for the
remaining modules.

## Goal

The final goal is a clean, contract-owned JSON-RPC control surface for every
long-running business module, with no old REST control machinery left in the
business runtime.

When this goal is complete:

- no business module uses `ConfluxEvent::Rest`;
- no business module implements `RestContract` or a module-specific
  `*HttpControl` codec;
- no server or composition path uses `with_http_control`,
  `HttpControlConfig`, or `HttpControlledConflux`;
- control commands and queries are defined once in the owner module's
  `contract/` crate;
- server-side control handling enters the owning Actor through
  `RpcActorInvocation`, preserving the single-writer rule;
- Conflux owns runtime concerns such as listeners, shutdown, request timeout,
  health files, and socket cleanup;
- application code owns only typed business handling and never matches HTTP
  methods, paths, status codes, query strings, or request bodies.

The desired end state is not "REST wrapped in JSON-RPC". It is a narrower
runtime boundary: contract defines the process API, Conflux serializes calls
into the Actor, and the business module implements typed use-case methods.

## Current Reference shape

Reference demonstrates the target pattern:

- `kairos_reference_contract` owns the `ReferenceControlRpc` trait.
- The trait uses `#[conflux_rpc(namespace = "reference")]`.
- The generated binding macro is invoked in `reference/src/application/mod.rs`
  to create `ReferenceRpcActor` and `ReferenceRpcService`.
- `ReferenceApplication` implements `ReferenceRpcActor` in
  `reference/src/application/conflux.rs`.
- `ConfluxActor::handle` handles runtime events only, such as timers and
  integration/system events.
- The Reference server creates an actor invocation handle and exposes the
  generated RPC service through `with_json_rpc`.
- Reference publication is declared on `ConfluxSystem.outputs()` and does not
  require a separate application-owned writer facade.

Use this shape before inventing a new module-specific control adapter.

## Required module shape

Each migrated module should have this structure.

In the contract crate:

- define one RPC trait for the module control surface;
- use `#[conflux_rpc(namespace = "<module>")]`;
- keep request, response, snapshot, event, and control error DTOs owned by the
  module contract;
- expose generated client/server traits through the contract crate;
- do not expose domain entities, application types, service instances,
  persistence rows, provider SDK payloads, or transport records.

In the main module crate:

- invoke the generated `<control_rpc>_conflux_actor!` macro from
  `application/mod.rs`;
- implement the generated RPC actor trait for the owning application/process;
- map contract requests into application commands or queries explicitly;
- map application/domain errors into contract control errors and then into
  JSON-RPC business errors;
- keep `ConfluxActor::handle` focused on system, integration, contract event,
  timer, and source processing;
- publish any snapshots/events that the old REST handler published.

In composition and binaries:

- construct `Conflux` and obtain `ConfluxHandle`;
- create `handle.rpc_actor_invocation(timeout)`;
- construct the module RPC service with that invocation handle;
- call `with_json_rpc` with the generated methods and configured listeners;
- declare outputs and connections in composition;
- keep binaries limited to argument parsing, composition, and process launch.

## Migration steps

Apply these steps one module at a time.

1. Confirm the module owner boundary.
   Identify the owning contract crate, main application/process type, mutable
   state owner, publication outputs, and current control methods.

2. Define or update the contract RPC trait.
   Replace hand-written HTTP codec entry points with a typed JSON-RPC trait.
   Preserve operation names, idempotency semantics, and contract DTOs unless
   there is an explicit contract decision to change them.

3. Generate the Conflux RPC actor binding.
   Invoke the generated macro in the module's application boundary and export
   the generated service type only where composition/server code needs it.

4. Split old `handle_rest` logic into RPC actor methods.
   Each RPC method should do one typed operation, return `RpcResult<T>`, and
   use `RpcRequest::into_parts()` when it needs access to `Context`.

5. Remove control handling from `ConfluxActor::handle`.
   Delete `ConfluxEvent::Rest` handling and make the actor event handler return
   `Result<(), FatalError>`.

6. Update server and composition.
   Replace `with_http_control` with `with_json_rpc`; remove `HttpControlConfig`
   and `HttpControlledConflux` type aliases.

7. Update tests and CLIs.
   Replace direct `handle(ConfluxEvent::Rest(...))` calls with application API
   calls, generated RPC service calls, or focused test helpers that use
   `RpcActorInvocation`.

8. Delete old REST artifacts.
   Remove `RestContract`, `*Rest`, `*HttpControl`, HTTP codec tests, and stale
   exports once the module is fully migrated.

9. Add architecture checks.
   Each module should assert that old control symbols do not reappear and that
   the new JSON-RPC actor service is used by the host/server.

10. Run focused verification.
    Run the contract and main module tests first, then the relevant workspace
    checks.

## Recommended migration order

Risk should follow Reference. It has a compact control surface and no real
local event complication, so it is the best second sample.

Capital should follow Risk. Its control surface is larger, but its migration is
mostly a careful split of existing command handling into RPC actor methods.

Execution and Account should follow after the pattern has two stable examples.
Both modules have more side effects around external connections, publication,
leases, audit, freshness, and reconciliation, so they need stricter regression
checks.

Market should be migrated last unless its local source-input model is resolved
earlier. Market currently has module-private local events, and the new Conflux
event model no longer carries a generic local event. Decide how source input
enters the Actor before migrating Market's control surface.

## Error handling

Each module should reserve a stable JSON-RPC business error code. The exact
values are less important than making them unique, documented, and tested.

The mapping should follow this path:

```text
application/domain error
  -> module contract control error
  -> JSON-RPC business error object
```

Transport failures, timeouts, closed queues, and stopped actors remain platform
or runtime errors. They should not be encoded as successful business responses.

Do not use `serde_json::Value` as the core control payload. It is acceptable
only for explicitly named diagnostics, extensions, or provider-specific
details.

## Completion criteria

A module is migrated only when all of the following are true:

- its contract crate owns the JSON-RPC control trait;
- its application/process implements the generated RPC actor trait;
- its `ConfluxActor::handle` does not process control requests;
- its host/server uses `with_json_rpc`;
- old HTTP control codec types are deleted or no longer exported;
- old REST contract marker types are deleted;
- CLIs and tests no longer submit `ConfluxEvent::Rest`;
- publication side effects from the old handlers are preserved;
- architecture tests prevent the old control path from returning;
- focused contract and module tests pass.

## Prohibited patterns

Do not add new business-module usages of:

- `RestContract`;
- `ConfluxEvent::Rest`;
- `RestRequestOf` or `RestResponseOf`;
- `HttpControlConfig`;
- `HttpControlledConflux`;
- `with_http_control`;
- module-specific `*HttpControl` codecs;
- Axum routes or HTTP listener code in business modules;
- application methods that receive HTTP method, path, query, status, headers,
  or body bytes;
- generic JSON envelopes for core commands, queries, events, snapshots, or
  admissions.

These names may appear only while deleting the old design from unmigrated
modules or in compatibility notes that are not part of runtime code.

## Verification

For each migrated module, run the focused checks first:

```bash
cargo check -p <module-contract-crate> -p <module-main-crate>
cargo test -p <module-contract-crate> -p <module-main-crate>
```

Then run the repository checks relevant to the change:

```bash
git diff --check
python3 scripts/check/check_crate_layout.py
python3 scripts/check/check_workspace_dependencies.py
python3 scripts/check/check_documentation.py
```

Run broader workspace tests when the platform layer or shared macros change.
