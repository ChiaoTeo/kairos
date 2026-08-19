# Conflux connection ownership

- Status: Accepted
- Date: 2026-08-19

## Context

Provider connections were previously removed from Conflux-managed collections and
moved into module-owned Tokio workers. Once removed, Conflux could no longer own
their shutdown, maintenance, reconnect policy, generation, health, or event
polling. Account, Execution, Market, and Reference consequently gained separate
runtime ownership paths for the same platform capability.

Long-lived provider connections require one owner for their complete lifecycle,
including periodic maintenance and cooperative event polling. Business modules
still need typed access to provider capabilities during an actor turn, but must
not become connection owners or depend directly on Integration implementations.

## Decision

`ConfluxSystem` owns concrete Integration connections for their entire lifetime.
It creates and stores them in private typed collections, drives connection
maintenance and event polling, and disconnects them before removal. A connection
key and generation identify each managed instance; recreating a removed key uses
a higher generation.

Business actors borrow typed capability facades through their Conflux `Context`
only for the duration of a handler turn. Composition retains creation parameters
and connection keys, not concrete connections. Integration stream events enter
the same cooperative Conflux driver as maintenance; transport and local control
ingress remain independent asynchronous branches.

## Consequences

- Connection lifecycle, maintenance, recovery, health, and drop have one owner.
- Business modules cannot remove connections into private workers or keep a
  second production event loop.
- Account, Execution, Market, and Reference use typed Context capabilities and
  do not directly depend on the Integration crate.
- Dynamic removal must complete disconnect before the connection is dropped.
- Test and replay sources may keep local workers when they do not represent a
  production Integration connection.
