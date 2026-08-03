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

Before adding or moving code, read `docs/module-boundaries.md`. When reviewing imports, treat cross-module imports from `services` as violations unless the import is inside the same module. Run focused tests and static searches after boundary changes.
