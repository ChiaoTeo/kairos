# V2 root registry

All entries are `DRAFT`: identifiers are reserved for design and compile
validation, but no root is published or supported until its implementation,
cross-language fixtures, and migration exit criteria are complete. Reserved
identifiers must not be reused.

Current-view entries use the external resource topology and lifecycle defined
in [`mmap-contract.md`](./mmap-contract.md); a FlatBuffers root alone is not a
complete mmap contract.

Market and Execution control are intentionally not FlatBuffers roots. Their
UDS HTTP-shaped contracts are [`schemas/v2/market/control.openapi.yaml`](./market/control.openapi.yaml)
and [`schemas/v2/execution/control.openapi.yaml`](./execution/control.openapi.yaml):
JSON/OpenAPI defines command and bounded-status request/response semantics,
while FlatBuffers is reserved for business events and mmap views.

| Status | Owner | Shape | Semantic root | File identifier | Publisher/caller | Consumer | Transport/profile |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DRAFT | Reference | event | `InstrumentUpserted` | `RIU2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `InstrumentUpdated` | `RID2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `ListingUpserted` | `RLU2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `ListingUpdated` | `RLD2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `MarketUpserted` | `RMU2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `MarketUpdated` | `RMD2` | Reference Actor | Market Reference projection, operations | owner event stream / retained target |
| DRAFT | Reference | event | `AssetUpserted` | `RAU2` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `AssetUpdated` | `RAD2` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `EntityUpserted` | `RENU` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `EntityUpdated` | `REND` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Market | event | `QuoteUpdated` | `MQU2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `TradeOccurred` | `MTO2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `BarCompleted` | `MBV2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `GreeksUpdated` | `MGU2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `RateUpdated` | `MRU2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `Ticker24hUpdated` | `MTU2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `MarkPriceUpdated` | `MMP2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `FundingRateUpdated` | `MFR2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `OpenInterestUpdated` | `MOI2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `IndexPriceUpdated` | `MIP2` | Market Actor | Strategy Market application | Market event stream / ephemeral fail-closed initially |
| DRAFT | Market | event | `OrderBookSnapshotReceived` | `MOS2` | Market Actor | OrderBook application, execution preflight | Market event stream / gap-aware |
| DRAFT | Market | event | `OrderBookDeltaReceived` | `MOD2` | Market Actor | OrderBook application, execution preflight | Market event stream / gap-aware |
| DRAFT | Market | event | `OrderBookResyncRequired` | `MOR2` | Market Actor | OrderBook application, operations | Market event stream / explicit recovery fact |
| DRAFT | Market | latest view | `QuoteLatestView` | `MLQ2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | window view | `BarWindowView` | `MBW2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `GreeksLatestView` | `MLG2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `RateLatestView` | `MLR2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `Ticker24hLatestView` | `MLT2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `MarkPriceLatestView` | `MLM2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `FundingRateLatestView` | `MFD2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `OpenInterestLatestView` | `MLI2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `IndexPriceLatestView` | `MLP2` | Market Actor | Strategy bootstrap | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `OrderBookLatestView` | `MLO2` | Market Actor | OrderBook application, execution preflight | KSS1 mmap / one writer |
| DRAFT | Market | latest view | `MarketFreshnessLatestView` | `MLF2` | Market Actor | Execution preflight, Strategy | KSS1 mmap / one writer |
| DRAFT | Account | event | `BalanceUpserted` | `ABU2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target |
| DRAFT | Account | event | `BalanceRemoved` | `ABR2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target |
| DRAFT | Account | event | `EarnHoldingUpserted` | `AEH2` | Account Actor | Portfolio, Capital reconciliation | Account event stream / retained target |
| DRAFT | Account | event | `EarnHoldingRemoved` | `AER2` | Account Actor | Portfolio, Capital reconciliation | Account event stream / retained target |
| DRAFT | Account | event | `PositionUpserted` | `APU2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target |
| DRAFT | Account | event | `PositionRemoved` | `APR2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target |
| DRAFT | Account | event | `ValuationChanged` | `AVC2` | Account Actor | Strategy Account application, settlement audit | Account event stream / retained target |
| DRAFT | Account | event | `AccountStatusChanged` | `ASC2` | Account Actor | Strategy Account application, operations | Account event stream / retained target |
| DRAFT | Account | event | `ObservedOrderUpserted` | `AOU2` | Account Actor | reconciliation | Account event stream / retained target |
| DRAFT | Account | event | `ObservedOrderRemoved` | `AOR2` | Account Actor | reconciliation | Account event stream / retained target |
| DRAFT | Account | current view | `AccountCurrentView` | `AAV2` | Account Actor | Execution preflight, Strategy Account application | KSS1 mmap / one writer |
| DRAFT | Account | current view | `ObservedOrdersCurrentView` | `AOV2` | Account Actor | reconciliation | KSS1 mmap / one writer |
| DRAFT | Risk | command | `AuthorizeAndReserve` | `control.openapi.yaml` | Execution application | Risk application | UDS HTTP/JSON / atomic synchronous decision |
| DRAFT | Risk | command result | `AuthorizeAndReserveResponse` | `control.openapi.yaml` | Risk Actor | Execution application | UDS HTTP/JSON response |
| DRAFT | Risk | command | `ConsumeReservation` | `control.openapi.yaml` | Execution application | Risk application | UDS HTTP/JSON / idempotent retry |
| DRAFT | Risk | command result | `ReservationCleanupResponse` | `control.openapi.yaml` | Risk Actor | Execution application | UDS HTTP/JSON response |
| DRAFT | Risk | command | `ReleaseReservation` | `control.openapi.yaml` | Execution application | Risk application | UDS HTTP/JSON / idempotent retry |
| DRAFT | Risk | event | `RiskDecisionMade` | `RDV2` | Risk Actor | Strategy Risk application, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationReserved` | `RRV2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationConsumed` | `RRC2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationReleased` | `RRL2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationExpired` | `RRX2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `CircuitOpened` | `RKO2` | Risk Actor | Execution, operations | Risk event stream / retained target |
| DRAFT | Risk | event | `CircuitClosed` | `RKC2` | Risk Actor | Execution, operations | Risk event stream / retained target |
| DRAFT | Risk | latest view | `RiskLatestView` | `RXV2` | Risk Actor | Execution preflight, operations | KSS1 mmap / one writer |
| DRAFT | Execution | command | `SubmitExecutionIntent` | `control.openapi.yaml` | Strategy application | Execution application | UDS HTTP/JSON / no unsafe retry |
| DRAFT | Execution | command result | `CommandAccepted` | `control.openapi.yaml` | Execution Actor | Strategy application | UDS HTTP/JSON response |
| DRAFT | Execution | command | `CancelOrder` | `control.openapi.yaml` | Strategy application | Execution application | UDS HTTP/JSON / delivery certainty required |
| DRAFT | Execution | command | `ReplaceOrder` | `control.openapi.yaml` | Strategy application | Execution application | UDS HTTP/JSON / delivery certainty required |
| DRAFT | Execution | command | `ReconcileExecution` | `control.openapi.yaml` | operations/Execution application | Execution application | UDS HTTP/JSON / bounded retry |
| DRAFT | Execution | event | `IntentAccepted` | `EIA2` | Execution Actor | Strategy Execution application | Execution event stream / retained target |
| DRAFT | Execution | event | `IntentRejected` | `EIR2` | Execution Actor | Strategy Execution application | Execution event stream / retained target |
| DRAFT | Execution | event | `IntentLifecycleChanged` | `EIL2` | Execution Actor | Strategy Execution application, audit | Execution event stream / retained target |
| DRAFT | Execution | event | `PlanCreated` | `EPV2` | Execution Actor | Strategy Execution application, audit | Execution event stream / retained target |
| DRAFT | Execution | event | `OrderSubmitted` | `EOS2` | Execution Actor | Strategy, audit | Execution event stream / retained target |
| DRAFT | Execution | event | `OrderAccepted` | `EOA2` | Execution Actor | Strategy, Account correlation | Execution event stream / retained target |
| DRAFT | Execution | event | `OrderRejected` | `EOR2` | Execution Actor | Strategy, Account correlation | Execution event stream / retained target |
| DRAFT | Execution | event | `OrderCanceled` | `EOC2` | Execution Actor | Strategy, Account correlation | Execution event stream / retained target |
| DRAFT | Execution | event | `OrderExpired` | `EOX2` | Execution Actor | Strategy, Account correlation | Execution event stream / retained target |
| DRAFT | Execution | event | `FillRecorded` | `EFV2` | Execution Actor | Account settlement, Strategy | Execution event stream / retained target |
| DRAFT | Execution | event | `ReconciliationRequired` | `EXV2` | Execution Actor | operations, Strategy | Execution event stream / retained target |
| DRAFT | Execution | active view | `ActiveIntentsView` | `ECI2` | Execution Actor | Strategy query/operations | KSS1 mmap / one writer |
| DRAFT | Execution | active view | `ActiveOrdersView` | `ECO2` | Execution Actor | Strategy query/operations | KSS1 mmap / one writer |
| DRAFT | Execution | current view | `CurrentExecutionView` | `ECV2` | Execution Actor | CLI, Strategy, operations, reconciliation | KSS1 mmap / one writer |
| DRAFT | Capital | current view | `CapitalCurrentView` | `CPV2` | Capital Actor | Portfolio, Strategy, operations, audit | KSS1 mmap / one writer |
| DRAFT | Capital | event | `FundingObjectiveChanged` | `COV2` | Capital Actor | Strategy, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalDemandChanged` | `CDV2` | Capital Actor | Strategy, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPolicyChanged` | `CYV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalFactsObserved` | `CFV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalAvailabilityEvaluated` | `CAV2` | Capital Actor | Strategy, Portfolio | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalRouteChanged` | `CRV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanAuthorized` | `CPAV` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanStateChanged` | `CPSV` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanExpired` | `CPEV` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | System | current view | `SystemHealthCurrentView` | `SHV2` | System monitor | operations | KSS1 mmap / one writer |
| DRAFT | System | current view | `AlertsCurrentView` | `SAV2` | System monitor | operations | KSS1 mmap / one writer |

## Registry-wide bounds

- Event roots contain one fact. Account transitions remain bounded atomic
  change vectors; Reference uses one entity per event root.
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
| Reference upsert/update events | one canonical entity record | exactly one entity | catalog revision effective immediately on commit | retained target by catalog writer stream |
| Market observation events | one observation | exactly one fact | consumer applies kind-specific maximum source/receive age | ephemeral fail-closed initially; v2.1 requires retained cursor or owner resync |
| `OrderBookSnapshotReceived` | `(source_id, market_id)` | exactly one provider snapshot | source/receive time and snapshot sequence | owner resync after gap |
| `OrderBookDeltaReceived` | `(source_id, market_id)` | exactly one contiguous provider range | source/receive time and first/last sequence | owner resync after gap |
| `OrderBookResyncRequired` | `(source_id, market_id)` | exactly one gap fact | emitted when the current book is no longer trusted | explicit snapshot resync |
| Account change roots | one balance, position, valuation, status, or observed-order transition | exactly one fact | provider evidence is optional; owner transition time is in metadata | retained target per account stream |
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
| `QuoteLatestView` | one `(source_id, market_id)` quote identity | exactly one latest quote | Strategy policy compares source/receive/as-of time |
| `BarWindowView` | configured Market bar kind/window scope | bounded completed-bar window per source + market + window definition | only completed bars; Strategy policy checks window end |
| `GreeksLatestView` | one `(source_id, market_id)` Greeks identity | exactly one latest value | Strategy policy checks source/receive/as-of time |
| `OrderBookLatestView` | one `(source_id, market_id, instrument_id)` order-book identity | exactly one latest book | `synchronized` must be true for execution use |
