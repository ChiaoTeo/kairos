# CLI boundaries

This document defines the command-line ownership boundary for Kairos. It is
the companion to `docs/module-boundaries.md`: module business ownership stays
in Rust; this document defines how commands reach that ownership.

## Canonical owners

Every business module has one canonical Rust one-shot CLI:

| Surface | Canonical binary | Business owner |
| --- | --- | --- |
| reference | `kairospy reference` / `kairos-reference-cli` | Reference application |
| account | `kairos-account-cli` | Account application |
| market | `kairos-market-cli` | Market application |
| order/execution | `kairos-execution-cli` | Execution application |

The Rust binary owns its command tree, business options, provider selection,
endpoint selection, credential selection, and output format. The Python
`kairos` command is a system shell and transparent invocation surface; it must
not redeclare a business command tree.

Long-running processes are separate. `kairos launch` and `kairos system` own
process lifecycle and Unix-socket control. Typed Python system clients query a
running process; they do not own business state or one-shot command syntax.

## Argument ownership

An argument is assembled by exactly one layer:

| Argument family | Owner |
| --- | --- |
| `--workspace` for a Rust one-shot command | Rust CLI; Python adapter may bind it once when invoking the binary |
| `--provider`, `--product`, `--endpoint` | Rust business CLI/composition |
| credential and secret options | Rust CLI/composition; secret values should come from env or credential storage |
| `--output` / `--format` | Rust CLI for canonical commands |
| launch mode, instance id, socket, health file | System/process composition |
| no business argument | Python must not invent one |

Python native adapters that add `--workspace` or machine-readable output must
reject those same options in their input argv. They must never silently append
a second single-value option.

## Forwarding contract

Canonical forwarding has one contract:

```text
Python argv
  -> no business translation
  -> workspace binding exactly once
  -> Rust canonical argv
```

Rust provider defaults live in Rust composition and are shared by the one-shot
CLI and server.

For example:

```text
kairos-reference-cli --provider binance-spot refresh
  -> kairos-reference-cli --workspace <workspace> --provider binance-spot refresh
```

No other layer may inject `--provider`.

## Command policy

There are no Python legacy command aliases. If a command is missing from the
Rust CLI, implement it in the owning Rust application and then expose it by
transparent forwarding. Do not add a Python compatibility command to conceal
an incomplete business API.

New commands must be added to the Rust CLI first. Python may expose them by
transparent forwarding without adding a second Python parser.
