# V2 root registry

All entries are `DRAFT`: identifiers are reserved for design and compile
validation, but no root is published or supported until its implementation,
cross-language fixtures, and migration exit criteria are complete. Reserved
identifiers must not be reused.

Current-view entries use the external resource topology and lifecycle defined
in [`mmap-contract.md`](./mmap-contract.md); a FlatBuffers root alone is not a
complete mmap contract.

| Status | Owner | Shape | Semantic root | File identifier | Publisher/caller | Consumer | Transport/profile | Replaces |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DRAFT | Reference | event | `ReferenceChanged` | `RCV2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target | `RCH1` |
| DRAFT | Market | event | `QuoteObserved` | `MQV2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially | `MQT1` |
| DRAFT | Market | event | `TradeObserved` | `MTV2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially | `MTR1` |
| DRAFT | Market | event | `BarCompleted` | `MBV2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially | `MBA1` |
| DRAFT | Market | event | `GreeksObserved` | `MGV2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially | `MGR1` |
| DRAFT | Market | current view | `QuoteCurrentView` | `MCQ2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer | Quote part of `PMC1` |
| DRAFT | Market | current view | `BarCurrentView` | `MCB2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer | Bar part of `PMC1` |
| DRAFT | Market | current view | `GreeksCurrentView` | `MCG2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer | Greeks part of `PMC1` |
| DRAFT | Account | event | `AccountChanged` | `ACV2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target | `ACE1` |
| DRAFT | Account | current view | `AccountCurrentView` | `AAV2` | Account Actor | Execution preflight, Strategy Account application | KSS1 mmap / one writer | `AAC1` |
| DRAFT | Account | current view | `ObservedOrdersCurrentView` | `AOV2` | Account Actor | reconciliation | KSS1 mmap / one writer | `AAC1.open_orders` |
| DRAFT | Risk | command | `AuthorizeAndReserveCommand` | `RAV2` | Execution application | Risk application | command transport / no unsafe retry | `RAR3` |
| DRAFT | Risk | command result | `AuthorizeAndReserveResult` | `RSV2` | Risk Actor | Execution application | command response | `RRD1` response path |
| DRAFT | Risk | command | `ConsumeReservationCommand` | `RUV2` | Execution application | Risk application | command transport / idempotent retry | `RCR1` |
| DRAFT | Risk | command result | `ReservationCleanupResult` | `RTV2` | Risk Actor | Execution application | command response | implicit v1 response |
| DRAFT | Risk | command | `ReleaseReservationCommand` | `RLV2` | Execution application | Risk application | command transport / idempotent retry | `RLR1` |
| DRAFT | Risk | event | `RiskDecisionMade` | `RDV2` | Risk Actor | Strategy Risk application, audit | Risk event stream / retained target | decision part of `RKE1` |
| DRAFT | Risk | event | `ReservationChanged` | `RRV2` | Risk Actor | Execution, audit | Risk event stream / retained target | reservation part of `RKE1`, `RSE1` |
| DRAFT | Risk | event | `CircuitChanged` | `RKV2` | Risk Actor | Execution, operations | Risk event stream / retained target | circuit part of `RKE1` |
| DRAFT | Risk | current view | `RiskCurrentView` | `RXV2` | Risk Actor | Execution preflight, operations | KSS1 mmap / one writer | `PRK1` |
| DRAFT | Execution | command | `SubmitExecutionIntentCommand` | `EIV2` | Strategy application | Execution application | command transport / no unsafe retry | `OIR1` |
| DRAFT | Execution | command result | `SubmitExecutionIntentResult` | `ERV2` | Execution Actor | Strategy application | command response | implicit v1 response |
| DRAFT | Execution | event | `IntentLifecycleChanged` | `ELV2` | Execution Actor | Strategy Execution application | Execution event stream / retained target | intent part of `EXE1` |
| DRAFT | Execution | event | `PlanCreated` | `EPV2` | Execution Actor | Strategy Execution application, audit | Execution event stream / retained target | absent in v1 |
| DRAFT | Execution | event | `OrderLifecycleChanged` | `EOV2` | Execution Actor | Strategy, Account correlation | Execution event stream / retained target | order part of `EXE1` |
| DRAFT | Execution | event | `FillRecorded` | `EFV2` | Execution Actor | Account settlement, Strategy | Execution event stream / retained target | fill part of `EXE1`, `OFL1` |
| DRAFT | Execution | event | `ReconciliationRequired` | `EXV2` | Execution Actor | operations, Strategy | Execution event stream / retained target | absent in v1 |
| DRAFT | Execution | current view | `ActiveIntentsCurrentView` | `ECI2` | Execution Actor | Strategy query/operations | KSS1 mmap / one writer | `PIJ1` active slice under new owner |
| DRAFT | Execution | current view | `ActiveOrdersCurrentView` | `ECO2` | Execution Actor | Strategy query/operations | KSS1 mmap / one writer | `PEO1` active slice |
| DRAFT | System | current view | `SystemHealthCurrentView` | `SHV2` | System monitor | operations | KSS1 mmap / one writer | `RTH1` |
| DRAFT | System | current view | `AlertsCurrentView` | `SAV2` | System monitor | operations | KSS1 mmap / one writer | `RTA1` |

## Registry-wide bounds

- Event roots contain one fact, except `ReferenceChanged` and `AccountChanged`,
  whose change vectors represent one atomic owner transition and are bounded to
  1,024 variants by adapters.
- Market current-view row vectors are bounded by the configured subscription
  universe and must fit the configured KSS1 slot. Publication fails explicitly
  on overflow.
- Account, Risk, Execution, and System current-view vectors are bounded by
  workspace configuration. Each publisher records and validates its bound.
- All command request vectors have explicit adapter limits; Execution intents
  initially permit at most 64 legs and Risk decisions at most 256 allocations.

## Event details

| Root(s) | Logical key | Cardinality per event | Time/freshness | Recovery |
| --- | --- | --- | --- | --- |
| `ReferenceChanged` | one committed catalog revision | 1-1,024 atomic changes | revision effective immediately on commit | retained target by catalog writer stream |
| Market observation events | one observation | exactly one fact | consumer applies kind-specific maximum source/receive age | ephemeral fail-closed initially |
| `AccountChanged` | account ID + segment key | 1-1,024 atomic changes | observed time in values; owner transition time in metadata | retained target per account stream |
| Risk events | one decision, reservation transition, or circuit transition | exactly one fact | decision/transition time must be nonzero | retained target per Risk Actor stream |
| Execution events | one intent, plan, order, fill, or reconciliation fact | exactly one fact | source fill time is distinct from owner record time | retained target per Execution Actor stream |

## Command details

| Command | Logical target | Request bound | Deadline/idempotency |
| --- | --- | --- | --- |
| `SubmitExecutionIntentCommand` | one Execution Actor selected by composition | 1-64 unique legs; one optional hedge relation | deadline is explicit; retry only by idempotency key after delivery-unknown reconciliation |
| `AuthorizeAndReserveCommand` | one Risk Actor | 1-64 unique metric usages; result at most 256 allocations | caller deadline plus reservation TTL; authorization and reservation are atomic |
| `ConsumeReservationCommand` | one Risk reservation | one reservation ID | cleanup is idempotent and returns applied/already-terminal/not-found/rejected |
| `ReleaseReservationCommand` | one Risk reservation | one reservation ID | cleanup is idempotent and available while trade admission is degraded |

## Current-view details

| Root | View key semantics | Population bound | Freshness |
| --- | --- | --- | --- |
| `QuoteCurrentView` | configured Market quote scope | at most one latest quote per source + market | Strategy policy compares source/receive/as-of time |
| `BarCurrentView` | configured Market bar kind/window scope | at most one latest completed bar per source + market + window definition | only completed bars; Strategy policy checks window end |
| `GreeksCurrentView` | configured Market Greeks scope | at most one latest observation per source + market | Strategy policy checks source/receive/as-of time |
| `AccountCurrentView` | Account Actor scope | one row per configured account segment | live preflight rejects stale/unknown freshness |
| `ObservedOrdersCurrentView` | Account Actor reconciliation scope | configured maximum provider-observed open orders | observation age is explicit; not Execution truth |
| `RiskCurrentView` | Risk Actor scope | configured policies + live reservations + circuits | policy generation/as-of checked by Execution preflight |
| `ActiveIntentsCurrentView` | Execution Actor scope | non-terminal intents only | terminal intent history is queried, not retained in mmap |
| `ActiveOrdersCurrentView` | Execution Actor scope | non-terminal orders only | terminal order history is queried, not retained in mmap |
| `SystemHealthCurrentView` | workspace System monitor | configured Actors and connections | operational freshness only |
| `AlertsCurrentView` | workspace System monitor | configured active/acknowledged alerts | resolved alert history is queried |
