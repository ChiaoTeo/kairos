# Integration adapter references

This directory records how mature upstream adapters are used while migrating
Kairos Integration providers.

The authoritative architecture and migration rules are in
[`integration-session-and-operation-design.md`](../integration-session-and-operation-design.md).
These notes are supporting evidence, not an alternative API design.

## Required workflow

For each provider/product/capability slice:

1. Use the official provider documentation as the source of truth.
2. Inspect a mature adapter for implementation patterns and failure cases.
3. Record the exact upstream repository and commit before copying source.
4. Map behavior into Kairos-owned connection, capability, application, and
   domain types.
5. Add focused tests for normal operation, failure, recovery, and delivery
   certainty.
6. Delete the old path for the migrated slice after the exit criteria pass.

## What is reusable

- authentication and signing algorithms;
- REST and WebSocket protocol handling;
- rate-limit and time-synchronization behavior;
- reconnect, sequence, snapshot, replay, and resync handling;
- provider status and event normalization;
- fixtures and failure-path test cases.

## What is not a drop-in dependency

Do not import an upstream project's domain model, event bus, cache, runtime,
strategy API, or application architecture into Kairos. The upstream adapter is
a behavior reference. Kairos remains responsible for its own
`CommandOutcome`, `IntegrationError`, external facts, business composition,
and Actor ownership rules.

## Reference note template

Each provider note should contain:

```markdown
# Provider / product

## Upstream reference

- Repository:
- Branch/tag/commit:
- License:
- Relevant paths:

## Provider behavior reviewed

- authentication:
- command semantics:
- query semantics:
- stream semantics:
- rate limits:
- recovery and resync:

## Kairos mapping

| Upstream behavior | Kairos owner |
|---|---|

## Reuse record

- copied source:
- rewritten logic:
- tests adopted:
- local modifications:

## Deliberately not copied

## Exit criteria
```

When source code is copied, preserve its copyright and license notices and
record the local modifications. NautilusTrader source is LGPL-3.0; consult the
license before distributing copied or modified source.
