# Project scripts

Scripts are grouped by the kind of repository operation they perform:

| Directory | Purpose |
| --- | --- |
| [`check/`](check/) | Static architecture and async-boundary checks |
| [`generate/`](generate/) | FlatBuffers bindings, schema validation, and wire fixtures |
| [`docs/`](docs/) | Local OpenAPI documentation rendering |
| [`build/`](build/) | Rust binary builds and observability benchmarks |
| [`maintenance/`](maintenance/) | Explicit repair and local development setup tasks |

Run these commands from the repository root. The paths are also used by CI
and the relevant tests, so moving a script requires updating its callers.

Generated artifacts should remain outside `scripts/`; local API pages are
written to the ignored `target/docs/` directory. Language bindings required by
the Rust or Python build remain in their respective generated-source
directories and follow their own reproducibility checks.
