# Project Memory: Module Boundaries

This repository is migrating to a strict module contract:

- Every module exposes `application/`, keeps implementation in `services/`, and declares consumed dependencies in `protocol.py`.
- Cross-module callers may import only the target module's `application` API. Never import another module's `services` or private files.
- A module's service may implement an upstream/consumer-owned protocol, but the service implementation remains private. Implementing a protocol does not make the service public.
- Protocols belong to the consuming module and must be minimal. Implementations may live in services, adapters, infrastructure, persistence, or runtime composition.
- DDD domain must not depend on another business module, especially its application, services, protocols, infrastructure, or external implementation. It may depend on stable generic/shared-kernel capabilities.
- Cross-business-module collaboration belongs in application orchestration or composition. Do not hide a dependency on another business module's application inside a domain service.
- Concrete connectors, SDK clients, stores, and mode-specific implementations are selected and injected only by composition/factories/test fixtures.
- Core must not depend on application, infrastructure, or surface. Application code must not directly import concrete infrastructure or raw vendor payloads.
- Public application APIs use business-oriented request/result types and do not expose service instances, SDK types, vendor `params`, raw payloads, or persistence records.

## Application Facade vs. Internal Services

- A module's `application/` is its use-case boundary and public facade. "Public" means callable by code outside the module, not necessarily exposed to an end user through HTTP.
- Any cross-module caller must enter through the target module's `application/` API, even when the caller is another internal workflow, a CLI, a scheduled job, or a test fixture.
- `services/` contains private implementation details behind the application boundary. Do not move a capability to `services/` merely because it is not directly user-facing; move it there only when it is an implementation detail rather than a module use case.
- Keep business invariants and entity behavior in `domain/`; keep use-case orchestration, transaction boundaries, and cross-module collaboration at the application boundary; keep concrete implementation and integration details private in `services/`, adapters, infrastructure, or composition.
- A useful review question is: "Would a caller outside this module need to invoke this capability as a use case?" If yes, define a business-oriented API in `application/`; otherwise keep it private.

## Agent Execution Rules

Before adding, moving, or deleting code, read `docs/module-boundaries.md` completely. Treat it as the architecture baseline for DDD convergence.

For every change, the Agent must first identify the business owner, state owner, command/event/query type, and target layer. Do not start by adding a manager, coordinator, processor, callback, registry, or compatibility facade.

Use this order when implementing a business change:

1. Define or verify the domain rule.
2. Define the use-case application API.
3. Assign mutable runtime state to exactly one Actor.
4. Let the Actor own its data loop and publish events.
5. Keep System limited to composition and lifecycle.
6. Keep CLI limited to input adaptation and application invocation.
7. Delete obsolete concepts after migration.

Before reporting completion, run focused tests, `uv run pytest -q`, `git diff --check`, and static searches for cross-module imports from `services` or new generic orchestration concepts. Explain any intentional exception.

When a requested change appears to introduce a new layer or split, stop and validate whether an existing Domain, Usecase, Actor, Monitor, or composition boundary already owns the responsibility. Prefer convergence and deletion over another indirection.
