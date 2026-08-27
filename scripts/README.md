# Project scripts

Scripts are grouped by the kind of repository operation they perform:

| Directory | Purpose |
| --- | --- |
| [`check/`](check/) | Static architecture and async-boundary checks |
| [`generate/`](generate/) | Rust FlatBuffers code, schema validation, and wire fixtures |
| [`docs/`](docs/) | Local OpenAPI documentation rendering |
| [`build/`](build/) | Rust binary builds and observability benchmarks |
| [`maintenance/`](maintenance/) | Explicit repair and local development setup tasks |

Execution venue transaction records are checked by
`check/check_execution_venue_certification.py`; it binds every `T` mark in the maintained matrix to a
complete, redacted, content-addressed record under `docs/integrations/execution-certifications/`.

Run these commands from the repository root. The paths are also used by CI
and the relevant tests, so moving a script requires updating its callers.

Generated artifacts should remain outside `scripts/`; local API pages are
written to the ignored `target/docs/` directory. Generated Rust wire code remains
in its protocol crate; Python consumes owner contracts through their native PyO3
companions and does not generate FlatBuffers bindings.
