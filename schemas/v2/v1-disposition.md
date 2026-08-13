# Complete v1 root disposition

This table makes the v2 design complete without copying every speculative v1
root. `REPLACE` and `SPLIT` have concrete v2 draft schemas. `QUERY/DATASET`
removes history or ledger data from mmap. `DEFER` requires a named public
application consumer before a v2 root is designed. `RETIRE` has no v2 wire
equivalent.

| V1 identifier | V1 root | Decision | V2 result and reason |
| --- | --- | --- | --- |
| `ACE1` | `AccountEvent` | REPLACE | `ACV2 AccountChanged`; typed atomic change union |
| `AAC1` | `AccountsSnapshot` | SPLIT | `AAV2 AccountCurrentView` plus `AOV2 ObservedOrdersCurrentView`; no Execution lifecycle ownership |
| `AAE1` | `EquitySnapshot` | QUERY/DATASET | Equity history is time-series/reporting data, not current mmap state |
| `OIR1` | `OrderIntentMessage` | REPLACE | `EIV2 SubmitExecutionIntentCommand`; complete multi-leg intent semantics |
| `OFL1` | `OrderFilledMessage` | REPLACE | `EFV2 FillRecorded`; full intent/plan/leg/order/account correlation |
| `EXE1` | `ExecutionEventMessage` | SPLIT | `ELV2`, `EPV2`, `EOV2`, `EFV2`, and `EXV2`; no string discriminator |
| `PEO1` | `OrdersSnapshot` | SPLIT | `ECO2 ActiveOrdersCurrentView`; terminal order history moves to query/audit |
| `PEF1` | `FillsSnapshot` | QUERY/DATASET | Fill ledger is append/history data until a measured mmap caller exists |
| `PIJ1` | `IntentSnapshot` | REPLACE | `ECI2 ActiveIntentsCurrentView` under Execution ownership; v2 has no Intent owner |
| `MQT1` | `QuoteMessage` | REPLACE | `MQV2 QuoteObserved` |
| `MTR1` | `TradeMessage` | REPLACE | `MTV2 TradeObserved`; retained because Strategy has a public consumer |
| `MBA1` | `BarMessage` | REPLACE | `MBV2 BarCompleted`; completion is explicit |
| `MGR1` | `GreeksMessage` | REPLACE | `MGV2 GreeksObserved` |
| `MOB1` | `OrderBookMessage` | DEFER | Requires a public application consumer and explicit snapshot/delta/resync semantics |
| `MRA1` | `RateMessage` | DEFER | Requires a named business consumer and a closed rate basis vocabulary |
| `MT24` | `Ticker24hMessage` | DEFER | Provider aggregate is not automatically a canonical business fact |
| `MMP1` | `MarkPriceMessage` | DEFER | Requires a valuation/risk caller and source/derivation semantics |
| `MIP1` | `IndexPriceMessage` | DEFER | Requires explicit index methodology/source semantics and caller |
| `MFR1` | `FundingRateMessage` | DEFER | Requires funding-period, effective-time, and settlement semantics |
| `MOI1` | `OpenInterestMessage` | DEFER | Requires contract unit and aggregation scope semantics |
| `MIS1` | `InstrumentStatusMessage` | DEFER | Must distinguish Reference lifecycle from Market source availability |
| `PMC1` | `MarketDataSnapshot` | SPLIT | `MCQ2`, `MCB2`, and `MCG2`; no omnibus mixed-cadence view |
| `PMH1` | `MarketHistorySnapshot` | QUERY/DATASET | Warm-up history belongs to bounded History/Dataset APIs |
| `PMB1` | `OrderBookSnapshot` | DEFER | Paired with deferred OrderBook event/resync design |
| `PMS1` | `SubscriptionsSnapshot` | RETIRE | Subscription control is Market operational state, not a Strategy business current view |
| `RAR1` | `AssessRiskRequest` | RETIRE | Trade path uses authoritative atomic authorization; non-mutating simulation may become a query later |
| `RAR2` | `RiskAssessmentResult` | RETIRE | Retired with standalone assessment command |
| `RAR3` | `AuthorizeRiskRequest` | REPLACE | `RAV2 AuthorizeAndReserveCommand`; multiple usages and complete context |
| `RRD1` | `RiskDecision` | REPLACE | `RSV2 AuthorizeAndReserveResult` and `RDV2 RiskDecisionMade` |
| `RRR1` | `ReserveRiskRequest` | RETIRE | Reservation is atomic with authorization, not a second race-prone command |
| `RCR1` | `ConsumeReservationRequest` | REPLACE | `RUV2 ConsumeReservationCommand` plus typed cleanup result |
| `RLR1` | `ReleaseReservationRequest` | REPLACE | `RLV2 ReleaseReservationCommand` plus typed cleanup result |
| `RSE1` | `ReservationEvent` | REPLACE | `RRV2 ReservationChanged`; complete reservation and transition |
| `RKE1` | `RiskEventMessage` | SPLIT | `RDV2`, `RRV2`, and `RKV2`; no string discriminator |
| `PRK1` | `RiskSnapshot` | REPLACE | `RXV2 RiskCurrentView`; lossless policy scopes and allocations |
| `RCH1` | `ReferenceChanged` | REPLACE | `RCV2 ReferenceChanged`; structured changes and SQLite revision, no JSON record payload |
| `RTH1` | `HealthSnapshot` | REPLACE | `SHV2 SystemHealthCurrentView`; operational only |
| `RTA1` | `AlertsSnapshot` | REPLACE | `SAV2 AlertsCurrentView`; operational only |
| `RTF1` | `FreshnessSnapshot` | RETIRE | Freshness remains owner-specific; System may aggregate health without becoming business truth |
| `RTO1` | `OperationsSnapshot` | QUERY/DATASET | Operation chains are audit/query data, not mutable business current state |

## Type-only v1 files

The remaining v1 files without roots (`common` headers/types, domain `types`,
and Reference entities) are not independently migrated. Their useful values
are redefined only inside the owning v2 contract. V2 does not preserve a type
merely because another v1 schema included it.
