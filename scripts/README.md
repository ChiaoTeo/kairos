# Project scripts

Scripts are grouped by the kind of repository operation they perform:

| Directory | Purpose |
| --- | --- |
| [`check/`](check/) | Static architecture and async-boundary checks |
| [`generate/`](generate/) | FlatBuffers bindings, schema validation, and wire fixtures |
| [`docs/`](docs/) | Market API documentation generation |
| [`build/`](build/) | Rust binary builds and observability benchmarks |

Run these commands from the repository root. The paths are also used by CI
and the relevant tests, so moving a script requires updating its callers.

Generated artifacts should remain outside `scripts/`; for example, API pages
are written to `docs/generated/` and language bindings are written to their
respective generated-source directories.
