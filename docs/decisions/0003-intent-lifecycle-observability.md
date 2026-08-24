# Decision 0003: Intent lifecycle observability

- Status: Accepted
- Date: 2026-08-18
- Scope: Strategy decisions, Execution intent lifecycle and notification policy

## Context

Intent admission, later execution transitions and strategy outcome evaluation
are different facts owned by different modules. Treating them as one lifecycle
loses causality and lets notification or Execution state incorrectly close a
Strategy-owned decision.

## Decision

- Strategy owns strategy decisions, progress and effect evaluation. Execution
  owns Intent, Plan, Order, Fill and execution terminal state.
- Strategy creates `strategy_decision_id`. Execution preserves it as an opaque,
  immutable causal link: one Intent has at most one source decision, while one
  decision may produce zero or more Intents.
- Execution persists each real lifecycle transition before publication.
  `IntentAccepted` and `IntentRejected` describe admission only; subsequent
  transitions use `IntentLifecycleChanged`.
- Execution terminal state does not close a Strategy decision. Strategy marks
  execution completion after all related Intents are terminal and evaluates
  outcomes on Strategy-owned horizons.
- The first horizon is `execution-final`; timed horizons use the Strategy
  business clock and recover from the decision journal.
- Machine-readable events, the decision journal and diagnostics remain active
  independently of human notification configuration.
- Strategy projects selected business facts into notifications. Execution
  never depends on notification transports or delivery results.
- Notification current view uses stable deduplication keys so replay does not
  create another logical notification.

## Consequences

Risk decisions remain Risk-owned authorization facts and cannot substitute for
strategy decisions. Effect evaluation cannot rewrite historical Execution
facts. The initial implementation stays within the existing Strategy process,
Execution event stream and notification worker; no new process or crate is
introduced without a current caller.

