# V2 root registry

All entries are `DRAFT`: identifiers are reserved for design and compile
validation, but no root is published or supported until its implementation,
cross-language fixtures, and migration exit criteria are complete. Reserved
identifiers must not be reused.

Current-view entries use named LMDB databases whose keys and per-entity value
roots are admitted separately. New entries are added only with a real
publisher, reader, and certification evidence.

Control contracts are intentionally not FlatBuffers roots. Each long-running
module owns a Rust `#[conflux_rpc]` trait exposed over workspace Unix
JSON-RPC. FlatBuffers is used for business events and admitted per-entity
current values; storage mechanics are defined by the current-view architecture.

| Status | Owner | Shape | Semantic root | File identifier | Publisher/caller | Consumer | Transport/profile |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DRAFT | Reference | event | `InstrumentUpserted` | `RIU2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `InstrumentUpdated` | `RID2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `ListingUpserted` | `RLU2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `ListingUpdated` | `RLD2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `MarketUpserted` | `RMU2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `MarketUpdated` | `RMD2` | Reference Actor | Market Reference catalog consumer, operations | owner event stream / retained target |
| DRAFT | Reference | event | `AssetUpserted` | `RAU2` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `AssetUpdated` | `RAD2` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `ExchangeUpserted` | `RENU` | Reference Actor | Reference consumers | owner event stream / retained target |
| DRAFT | Reference | event | `ExchangeUpdated` | `REND` | Reference Actor | Reference consumers | owner event stream / retained target |
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
| DRAFT | Market | indexed quote current value | `MarketQuoteCurrent` | `MQC3` | Market Actor | Strategy, Execution | LMDB `quotes` / one writer |
| DRAFT | Market | indexed bar current value | `MarketBarCurrent` | `MBC3` | Market Actor | Strategy | LMDB `bars`; latest completed bar per series |
| DRAFT | Market | indexed Greeks current value | `MarketGreeksCurrent` | `MGC3` | Market Actor | Strategy | LMDB `greeks` / one writer |
| DRAFT | Market | indexed rate current value | `MarketRateCurrent` | `MRC3` | Market Actor | Strategy | LMDB `rates` / one writer |
| DRAFT | Market | indexed ticker current value | `MarketTicker24hCurrent` | `MTC3` | Market Actor | Strategy | LMDB `tickers_24h` / one writer |
| DRAFT | Market | indexed mark-price current value | `MarketMarkPriceCurrent` | `MMP3` | Market Actor | Strategy | LMDB `mark_prices` / one writer |
| DRAFT | Market | indexed funding-rate current value | `MarketFundingRateCurrent` | `MFR3` | Market Actor | Strategy | LMDB `funding_rates` / one writer |
| DRAFT | Market | indexed open-interest current value | `MarketOpenInterestCurrent` | `MOI3` | Market Actor | Strategy | LMDB `open_interest` / one writer |
| DRAFT | Market | indexed index-price current value | `MarketIndexPriceCurrent` | `MIP3` | Market Actor | Strategy | LMDB `index_prices` / one writer |
| DRAFT | Market | indexed order-book current value | `MarketOrderBookCurrent` | `MOB3` | Market Actor | OrderBook, Execution | LMDB `order_books` / one writer |
| DRAFT | Market | indexed freshness current value | `MarketFreshnessCurrent` | `MFS3` | Market Actor | Strategy, operations | LMDB `freshness` / one writer |
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
| DRAFT | Account | indexed current value | `AccountSegmentCurrent` | `ASG3` | Account Actor | Execution preflight, Strategy, operations | LMDB `segments`; keyed by SegmentKey |
| DRAFT | Account | indexed current value | `AccountBalanceCurrent` | `ABA3` | Account Actor | Execution, Capital, Strategy | LMDB `balances`; keyed by SegmentKey and AssetId |
| DRAFT | Account | indexed current value | `AccountCollateralCurrent` | `ACO3` | Account Actor | Capital, Strategy | LMDB `collateral`; keyed by SegmentKey and AssetId |
| DRAFT | Account | indexed current value | `AccountPositionCurrent` | `APO3` | Account Actor | Execution, Strategy | LMDB `positions`; keyed by SegmentKey, InstrumentId, and PositionSide |
| DRAFT | Account | indexed current value | `AccountValuationCurrent` | `AVL3` | Account Actor | Capital, Strategy | LMDB `valuations`; keyed by SegmentKey |
| DRAFT | Account | indexed current value | `AccountEarnHoldingCurrent` | `AEH3` | Account Actor | Capital, Strategy | LMDB `earn_holdings`; keyed by SegmentKey and holding identity |
| DRAFT | Account | indexed current value | `AccountObservedOrderCurrent` | `AOO3` | Account Actor | Execution reconciliation, operations | LMDB `observed_orders`; keyed by SegmentKey, SourceId, and observation identity |
| DRAFT | Risk | command | `AuthorizeAndReserve` | `RiskControlRpc` | Execution application | Risk application | Unix JSON-RPC / atomic synchronous decision |
| DRAFT | Risk | command result | `AuthorizeAndReserveResponse` | `RiskControlRpc` | Risk Actor | Execution application | Unix JSON-RPC response |
| DRAFT | Risk | command | `ConsumeReservation` | `RiskControlRpc` | Execution application | Risk application | Unix JSON-RPC / idempotent retry |
| DRAFT | Risk | command result | `ReservationCleanupResponse` | `RiskControlRpc` | Risk Actor | Execution application | Unix JSON-RPC response |
| DRAFT | Risk | command | `ReleaseReservation` | `RiskControlRpc` | Execution application | Risk application | Unix JSON-RPC / idempotent retry |
| DRAFT | Risk | event | `RiskDecisionMade` | `RDV2` | Risk Actor | Strategy Risk application, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationReserved` | `RRV2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationConsumed` | `RRC2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationReleased` | `RRL2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `ReservationExpired` | `RRX2` | Risk Actor | Execution, audit | Risk event stream / retained target |
| DRAFT | Risk | event | `CircuitOpened` | `RKO2` | Risk Actor | Execution, operations | Risk event stream / retained target |
| DRAFT | Risk | event | `CircuitClosed` | `RKC2` | Risk Actor | Execution, operations | Risk event stream / retained target |
| DRAFT | Risk | indexed current value | `RiskStateCurrent` | `RSM3` | Risk Actor | Capital, operations | LMDB `state`; keyed by ActorId |
| DRAFT | Risk | indexed current value | `RiskPolicyCurrent` | `RPO3` | Risk Actor | Capital, operations | LMDB `policies`; keyed by PolicyId |
| DRAFT | Risk | indexed current value | `RiskLimitUsageCurrent` | `RLU3` | Risk Actor | Capital, operations | LMDB `limit_usage`; keyed by PolicyId |
| DRAFT | Risk | indexed current value | `RiskAllocationCurrent` | `RAL3` | Risk Actor | operations | LMDB `allocations`; keyed by ReservationId, PolicyId, and Metric |
| DRAFT | Risk | indexed current value | `RiskReservationCurrent` | `RRS3` | Risk Actor | Execution, operations | LMDB `reservations`; keyed by ReservationId |
| DRAFT | Risk | indexed current value | `RiskCircuitCurrent` | `RCI3` | Risk Actor | Execution, operations | LMDB `circuits`; keyed by canonical CircuitScope |
| DRAFT | Execution | command | `SubmitExecutionIntent` | `ExecutionControlRpc` | Strategy application | Execution application | Unix JSON-RPC / no unsafe retry |
| DRAFT | Execution | command result | `CommandAccepted` | `ExecutionControlRpc` | Execution Actor | Strategy application | Unix JSON-RPC response |
| DRAFT | Execution | command | `CancelOrder` | `ExecutionControlRpc` | Strategy application | Execution application | Unix JSON-RPC / delivery certainty required |
| DRAFT | Execution | command | `ReplaceOrder` | `ExecutionControlRpc` | Strategy application | Execution application | Unix JSON-RPC / delivery certainty required |
| DRAFT | Execution | command | `ReconcileExecution` | `ExecutionControlRpc` | operations/Execution application | Execution application | Unix JSON-RPC / bounded retry |
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
| DRAFT | Execution | indexed current value | `ExecutionOrderCurrent` | `EOR3` | Execution Actor | CLI, Strategy, operations, reconciliation | LMDB `orders`; keyed by OrderId |
| DRAFT | Execution | indexed current value | `ExecutionIntentCurrent` | `EIN3` | Execution Actor | CLI, Strategy | LMDB `intents`; keyed by IntentId |
| DRAFT | Execution | indexed current value | `ExecutionAlgorithmRunCurrent` | `EAR3` | Execution Actor | Strategy, operations | LMDB `algorithm_runs`; keyed by AlgorithmRunId |
| DRAFT | Execution | indexed current value | `ExecutionCommitmentCurrent` | `ECO3` | Execution Actor | Account correlation, operations | LMDB `commitments`; keyed by OrderId |
| DRAFT | Execution | indexed current value | `ExecutionRiskReservationCurrent` | `ERR3` | Execution Actor | Risk correlation, operations | LMDB `risk_reservations`; keyed by ReservationId |
| DRAFT | Execution | indexed current value | `ExecutionUnknownRemoteOrderCurrent` | `EUR3` | Execution Actor | reconciliation, operations | LMDB `unknown_remote_orders`; keyed by remote order identity |
| DRAFT | Capital | indexed state current value | `CapitalStateCurrent` | `CSM3` | Capital Actor | Capital contract readers | LMDB `state` / one writer |
| DRAFT | Capital | indexed objective current value | `CapitalObjectiveCurrent` | `CFO3` | Capital Actor | Capital contract readers | LMDB `objectives` / one writer |
| DRAFT | Capital | indexed demand current value | `CapitalDemandCurrent` | `CDM3` | Capital Actor | Capital contract readers | LMDB `demands` / one writer |
| DRAFT | Capital | indexed policy current value | `CapitalPolicyCurrent` | `CPC3` | Capital Actor | Capital contract readers | LMDB `policies` / one writer |
| DRAFT | Capital | indexed facts current value | `CapitalFactsCurrent` | `CFC3` | Capital Actor | Capital contract readers | LMDB `facts` / one writer |
| DRAFT | Capital | indexed availability current value | `CapitalAvailabilityCurrent` | `CAV3` | Capital Actor | Capital contract readers | LMDB `availability` / one writer |
| DRAFT | Capital | indexed route current value | `CapitalRouteCurrent` | `CRT3` | Capital Actor | Capital contract readers | LMDB `routes` / one writer |
| DRAFT | Capital | indexed plan current value | `CapitalPlanCurrent` | `CPL3` | Capital Actor | Capital contract readers | LMDB `plans` / one writer |
| DRAFT | Capital | indexed reservation current value | `CapitalReservationCurrent` | `CRS3` | Capital Actor | Capital contract readers | LMDB `reservations` / one writer |
| DRAFT | Capital | indexed operation current value | `CapitalOperationCurrent` | `COP3` | Capital Actor | Capital contract readers | LMDB `operations` / one writer |
| DRAFT | Capital | indexed alert current value | `CapitalAlertCurrent` | `CAL3` | Capital Actor | Capital contract readers | LMDB `alerts` / one writer |
| DRAFT | Capital | event | `FundingObjectiveChanged` | `COV2` | Capital Actor | Strategy, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalDemandChanged` | `CDV2` | Capital Actor | Strategy, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPolicyChanged` | `CYV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalFactsObserved` | `CFV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalAvailabilityEvaluated` | `CAV2` | Capital Actor | Strategy, Portfolio | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalRouteChanged` | `CRV2` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanAuthorized` | `CPAV` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanStateChanged` | `CPSV` | Capital Actor | operations, audit | Capital event stream / retained target |
| DRAFT | Capital | event | `CapitalPlanExpired` | `CPEV` | Capital Actor | operations, audit | Capital event stream / retained target |

## Registry-wide bounds

- Event roots contain one fact. Account transitions remain bounded atomic
  change vectors; Reference uses one entity per event root.
- Indexed current values are bounded by each owner's configured population and
  retention rules; one owner change commits as one LMDB transaction.
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
| `MarketQuoteCurrent` in `quotes` | one `(scope_key, provider, qualifier)` quote identity | exactly one latest quote per key | Strategy policy compares source/receive/as-of time |
| `MarketBarCurrent` in `bars` | configured Market bar series identity | exactly one latest completed bar per series | Strategy builds rolling windows from `BarCompleted` events and checks window end |
| `MarketGreeksCurrent` in `greeks` | one `(scope_key, provider, qualifier)` Greeks identity | exactly one latest value per key | Strategy policy checks source/receive/as-of time |
| `MarketOrderBookCurrent` in `order_books` | one `(scope_key, provider, qualifier)` order-book identity | exactly one latest book per key | `synchronized` must be true for execution use |
