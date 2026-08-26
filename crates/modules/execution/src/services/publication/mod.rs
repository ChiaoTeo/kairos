//! Concrete Execution v2 publication services.
//!
//! The application layer still owns business values.  This module is the
//! composition boundary where those values will be encoded into the v2
//! contract and sent over Aeron or mmap.  Keeping the adapters here prevents
//! generated FlatBuffers types from leaking into the Actor.

use flatbuffers::FlatBufferBuilder;
use kairos_execution_contract::{EncodeContext, ExecutionViewKey, event_metadata, view_metadata};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2 as common_fb;
use kairos_protocol::generated::kairos::execution::v_2 as fb;

use crate::application::ExecutionCurrentView;
use crate::domain::{
    CommitmentBasis, CommitmentResource, CommitmentStatus, ExecutionOrder, ExecutionOrderStatus,
    OrderCommitment, OrderSide, OrderType, RiskReservationEvidence, RiskReservationSagaStatus,
};

mod encoding;
mod events;

pub(crate) use encoding::*;
pub(crate) use events::encode_business_change;

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use kairos_execution_contract::ExecutionViewKind;
    use kairos_primitives::account::{AccountId, PositionSide, SegmentKey};
    use kairos_primitives::decimal::{Money, Price, Quantity};
    use kairos_primitives::execution::{IntentId, LegId, OrderId};
    use kairos_primitives::reference::{Currency, InstrumentId, MarketId};
    use kairos_primitives::time::{DurationNanos, Generation, Sequence, UnixNanos};

    use super::*;
    use crate::application::{
        DependencyWatermarks, ExecuteStrategyIntent, ExecutionBusinessChange, IntentEvent,
        IntentExecutionBenchmark, IntentState, IntentStatus, SnapshotWatermark,
    };
    use crate::domain::{
        AlgorithmExecutionQuality, AlgorithmLegBenchmark, AlgorithmLegBenchmarkQuality,
        AlgorithmLegExecutionQuality, AlgorithmLegLifecycle, AlgorithmRun, AlgorithmRunStatus,
        CommitmentBasis, CommitmentResource, ExecutionBenchmarkKind, ExecutionFeeTotal,
        OrderCommitment, RiskReservationEvidence, RiskReservationSagaStatus,
    };

    #[test]
    fn active_view_status_sets_exclude_terminal_history() {
        use crate::application::IntentStatus;
        use crate::services::publication::encoding::{
            active_intent_status, active_order_status, active_risk_reservation_status,
        };

        assert!(active_order_status(ExecutionOrderStatus::Unknown));
        assert!(!active_order_status(ExecutionOrderStatus::Filled));
        assert!(!active_order_status(ExecutionOrderStatus::Failed));
        assert!(active_intent_status(IntentStatus::ReconciliationRequired));
        assert!(!active_intent_status(IntentStatus::Satisfied));
        assert!(!active_intent_status(IntentStatus::Failed));
        assert!(active_risk_reservation_status(
            RiskReservationSagaStatus::Uncertain
        ));
        assert!(!active_risk_reservation_status(
            RiskReservationSagaStatus::Released
        ));
        assert!(!active_risk_reservation_status(
            RiskReservationSagaStatus::Failed
        ));
    }

    #[test]
    fn current_execution_mmap_excludes_terminal_and_released_state() {
        let order_id = OrderId::new("order-1").unwrap();
        let account_id = AccountId::new("account-1").unwrap();
        let segment_key = SegmentKey::new("spot").unwrap();
        let instrument_id = InstrumentId::new("BTC-USDT").unwrap();
        let mut commitment = OrderCommitment::new(
            order_id.clone(),
            account_id.clone(),
            segment_key,
            instrument_id.clone(),
            OrderSide::Buy,
            CommitmentResource::CloseablePosition {
                instrument_id: instrument_id.clone(),
                position_side: PositionSide::Long,
            },
            Money::new(100, 0).unwrap(),
            Quantity::new(1, 0).unwrap(),
            CommitmentBasis::CloseablePositionQuantity,
            UnixNanos::new(10),
        )
        .unwrap();
        commitment.settlement_asset = Some(Currency::new("USDT").unwrap());
        let mut released_commitment = commitment.clone();
        released_commitment.status = crate::domain::CommitmentStatus::Released;
        let mut active_order = ExecutionOrder::new(
            "active-order",
            "account-1",
            "spot",
            "BTC-USDT",
            OrderSide::Buy,
            OrderType::Limit,
            Quantity::new(1, 0).unwrap(),
            10,
        )
        .unwrap();
        active_order.status = ExecutionOrderStatus::Accepted;
        let mut terminal_order = ExecutionOrder::new(
            "terminal-order",
            "account-1",
            "spot",
            "BTC-USDT",
            OrderSide::Buy,
            OrderType::Limit,
            Quantity::new(1, 0).unwrap(),
            10,
        )
        .unwrap();
        terminal_order.status = ExecutionOrderStatus::Filled;
        terminal_order.filled_quantity = terminal_order.quantity;
        let active_reservation = RiskReservationEvidence {
            order_id,
            reservation_id: kairos_primitives::risk::ReservationId::new("execution:order-1")
                .unwrap(),
            idempotency_key: kairos_primitives::runtime::IdempotencyKey::new("execution:order-1")
                .unwrap(),
            account_id,
            amount: Money::new(100, 0).unwrap(),
            status: RiskReservationSagaStatus::Active,
            risk_generation: 7.into(),
            risk_event_sequence: 8.into(),
            policy_version: 2.into(),
            expires_at_unix_nanos: UnixNanos::new(1000),
            updated_at_unix_nanos: UnixNanos::new(11),
            funding_requirement: None,
        };
        let mut released_reservation = active_reservation.clone();
        released_reservation.status = RiskReservationSagaStatus::Released;
        let snapshot = ExecutionCurrentView {
            generation: Generation::new(3),
            event_sequence: Sequence::new(4),
            business_time_unix_nanos: None,
            orders: vec![active_order, terminal_order],
            commitments: vec![commitment, released_commitment],
            risk_reservations: vec![active_reservation, released_reservation],
            intents: Vec::new(),
            events: Vec::new(),
            intent_events: Vec::new(),
            fills: Vec::new(),
            algorithm_runs: Vec::new(),
            unknown_remote_orders: Vec::new(),
            exchange_event_watermark_unix_nanos: UnixNanos::new(0),
        };
        let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();
        let current_key =
            ExecutionViewKey::from_identity(&identity, ExecutionViewKind::CurrentExecution);
        let bytes =
            encode_current_execution("execution", &identity, 3, &current_key, &snapshot).unwrap();
        let current = fb::root_as_current_execution_view(&bytes).unwrap();
        assert_eq!(current.metadata().generation(), 3);
        assert_eq!(current.metadata().applied_revision(), Some(4));
        assert_eq!(current.orders().len(), 1);
        assert_eq!(current.orders().get(0).order_id(), "active-order");
        assert_eq!(current.commitments().len(), 1);
        assert_eq!(current.risk_reservations().len(), 1);
        assert_eq!(
            current.commitments().get(0).lifecycle(),
            fb::CommitmentLifecycle::HELD_BEFORE_SEND
        );
        assert_eq!(
            current.commitments().get(0).settlement_asset(),
            Some("USDT")
        );
        assert_eq!(
            current.commitments().get(0).resource_kind(),
            fb::CommitmentResourceKind::CLOSEABLE_POSITION
        );
        assert_eq!(
            current.commitments().get(0).position_side(),
            fb::CommitmentPositionSide::LONG
        );
        assert_eq!(
            current.risk_reservations().get(0).lifecycle(),
            fb::RiskReservationSagaLifecycle::ACTIVE
        );
    }

    #[test]
    fn algorithm_run_quality_is_published_in_current_execution_view() {
        let intent_id = IntentId::new("intent-quality").unwrap();
        let leg_id = LegId::new("leg-quality").unwrap();
        let mut run = AlgorithmRun::immediate(
            intent_id.clone(),
            [(leg_id.clone(), Quantity::new(20, 0).unwrap())],
        )
        .unwrap();
        run.status = AlgorithmRunStatus::Running;
        run.decision_sequence = 2;
        run.last_decision_at = Some(UnixNanos::new(125));
        run.legs[0].lifecycle = AlgorithmLegLifecycle::Active;
        run.legs[0].filled_quantity = Quantity::new(10, 0).unwrap();
        run.legs[0].benchmark = Some(AlgorithmLegBenchmark {
            kind: ExecutionBenchmarkKind::Arrival,
            instrument_id: InstrumentId::new("BTC-USDT").unwrap(),
            market_id: MarketId::new("market:test:BTC-USDT").unwrap(),
            price: Price::new(100, 0).unwrap(),
            observed_at_unix_nanos: UnixNanos::new(90),
        });
        run.quality = AlgorithmExecutionQuality {
            legs: vec![AlgorithmLegExecutionQuality {
                leg_id,
                order_count: 1,
                fill_count: 2,
                cancel_attempt_count: 1,
                filled_quantity: Quantity::new(10, 0).unwrap(),
                gross_notional: Money::new(1012, 0).unwrap(),
                average_fill_price: Some(Price::new(1012, 1).unwrap()),
                first_order_submitted_at: Some(UnixNanos::new(100)),
                first_fill_at: Some(UnixNanos::new(110)),
                last_fill_at: Some(UnixNanos::new(120)),
                time_to_first_fill: Some(DurationNanos::new(10)),
                time_to_last_fill: Some(DurationNanos::new(20)),
                fee_totals: vec![ExecutionFeeTotal {
                    currency: Currency::new("USDT").unwrap(),
                    amount: Money::new(3, 0).unwrap(),
                }],
                benchmark: Some(AlgorithmLegBenchmarkQuality {
                    kind: ExecutionBenchmarkKind::Arrival,
                    instrument_id: InstrumentId::new("BTC-USDT").unwrap(),
                    market_id: MarketId::new("market:test:BTC-USDT").unwrap(),
                    price: Price::new(100, 0).unwrap(),
                    observed_at_unix_nanos: UnixNanos::new(90),
                    benchmark_notional: Money::new(1000, 0).unwrap(),
                    implementation_shortfall: Some(Money::new(12, 0).unwrap()),
                }),
            }],
        };
        run.validate().unwrap();

        let mut intent = ExecuteStrategyIntent::test_fixture();
        intent.intent_id = intent_id;
        intent.strategy_id = "strategy-quality".into();
        intent.launch_id = "launch".into();
        intent.instance_id = "instance".into();
        intent.account_ids = vec![AccountId::new("main").unwrap()];
        let intent_state = IntentState {
            intent,
            status: IntentStatus::Executing,
            order_ids: Vec::new(),
            plan: None,
            completed_quantity: Quantity::new(10, 0).unwrap(),
            updated_at_unix_nanos: UnixNanos::new(125),
            reason: String::new(),
            dependency_watermarks: DependencyWatermarks::default(),
            pending_orders: Vec::new(),
            dormant_orders: Vec::new(),
            pending_order_due_unix_nanos: BTreeMap::new(),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            pending_quote_refresh: None,
            compensation_attempts: 0,
        };
        let terminal_intent_id = IntentId::new("intent-terminal").unwrap();
        let mut terminal_intent = intent_state.clone();
        terminal_intent.intent.intent_id = terminal_intent_id.clone();
        terminal_intent.status = IntentStatus::Satisfied;
        let mut terminal_run = run.clone();
        terminal_run.algorithm_run_id =
            crate::domain::AlgorithmRunId::for_intent(&terminal_intent_id);
        terminal_run.intent_id = terminal_intent_id;
        terminal_run.status = AlgorithmRunStatus::Completed;

        let snapshot = ExecutionCurrentView {
            generation: Generation::new(5),
            event_sequence: Sequence::new(8),
            business_time_unix_nanos: Some(UnixNanos::new(125)),
            orders: Vec::new(),
            commitments: Vec::new(),
            risk_reservations: Vec::new(),
            intents: vec![intent_state, terminal_intent],
            events: Vec::new(),
            intent_events: Vec::new(),
            fills: Vec::new(),
            algorithm_runs: vec![run, terminal_run],
            unknown_remote_orders: Vec::new(),
            exchange_event_watermark_unix_nanos: UnixNanos::new(120),
        };
        let identity = InstanceIdentity::new("workspace", "launch", "instance").unwrap();

        let current_key =
            ExecutionViewKey::from_identity(&identity, ExecutionViewKind::CurrentExecution);
        let current_bytes =
            encode_current_execution("execution", &identity, 5, &current_key, &snapshot).unwrap();
        let current = fb::root_as_current_execution_view(&current_bytes).unwrap();
        assert_eq!(current.intents().len(), 1);
        assert_eq!(current.algorithm_runs().len(), 1);
        assert_algorithm_quality(current.algorithm_runs().get(0));
    }

    fn assert_algorithm_quality(run: fb::AlgorithmRunState<'_>) {
        assert_eq!(run.algorithm_run_id(), "intent-quality:algorithm:1");
        assert_eq!(run.intent_id(), "intent-quality");
        assert_eq!(run.algorithm_kind(), "immediate");
        assert_eq!(run.lifecycle(), fb::AlgorithmRunLifecycle::RUNNING);
        assert_eq!(run.decision_sequence(), 2);
        assert_eq!(run.last_decision_at_unix_nanos(), Some(125));
        assert_eq!(run.legs().len(), 1);
        let leg = run.legs().get(0);
        assert_eq!(leg.leg_id(), "leg-quality");
        assert_eq!(leg.lifecycle(), fb::AlgorithmLegLifecycle::ACTIVE);
        let quality = leg.quality();
        assert_eq!(quality.order_count(), 1);
        assert_eq!(quality.fill_count(), 2);
        assert_eq!(quality.cancel_attempt_count(), 1);
        assert_eq!(quality.filled_quantity().mantissa(), 10);
        assert_eq!(quality.gross_notional().mantissa(), 1012);
        let average = quality.average_fill_price().unwrap();
        assert_eq!((average.mantissa(), average.scale()), (1012, 1));
        assert_eq!(quality.first_order_submitted_at_unix_nanos(), Some(100));
        assert_eq!(quality.first_fill_at_unix_nanos(), Some(110));
        assert_eq!(quality.last_fill_at_unix_nanos(), Some(120));
        assert_eq!(quality.time_to_first_fill_nanos(), Some(10));
        assert_eq!(quality.time_to_last_fill_nanos(), Some(20));
        assert_eq!(quality.fee_totals().len(), 1);
        assert_eq!(quality.fee_totals().get(0).currency(), "USDT");
        assert_eq!(quality.fee_totals().get(0).amount().mantissa(), 3);
        let benchmark = quality.benchmark().unwrap();
        assert_eq!(benchmark.kind(), fb::ExecutionBenchmarkKind::ARRIVAL);
        assert_eq!(benchmark.instrument_id(), "BTC-USDT");
        assert_eq!(benchmark.market_id(), "market:test:BTC-USDT");
        assert_eq!(
            (benchmark.price().mantissa(), benchmark.price().scale()),
            (100, 0)
        );
        assert_eq!(benchmark.observed_at_unix_nanos(), 90);
        assert_eq!(benchmark.benchmark_notional().mantissa(), 1000);
        assert_eq!(benchmark.implementation_shortfall().unwrap().mantissa(), 12);
    }

    #[test]
    fn lifecycle_event_preserves_correlation_previous_state_and_evidence() {
        let mut intent = ExecuteStrategyIntent::test_fixture();
        intent.intent_id = kairos_primitives::execution::IntentId::new("intent-1").unwrap();
        intent.strategy_id = "strategy-a".into();
        intent.strategy_decision_id = Some("strategy-a:decision:1".into());
        intent.launch_id = "launch-1".into();
        intent.instance_id = "instance-1".into();
        intent.account_ids = vec![AccountId::new("main").unwrap()];
        intent.instrument_id = InstrumentId::new("BTC-USDT").unwrap();
        intent.execution_benchmarks = vec![IntentExecutionBenchmark {
            kind: ExecutionBenchmarkKind::Arrival,
            leg_id: None,
            instrument_id: InstrumentId::new("BTC-USDT").unwrap(),
            market_id: MarketId::new("market:test:BTC-USDT").unwrap(),
            price: Price::new(100, 0).unwrap(),
            observed_at_unix_nanos: UnixNanos::new(9),
        }];
        let watermarks = DependencyWatermarks {
            account: BTreeMap::from([(
                "main".into(),
                SnapshotWatermark {
                    generation: Generation::new(7),
                    event_sequence: Sequence::new(11),
                },
            )]),
            market: Some(SnapshotWatermark {
                generation: Generation::new(8),
                event_sequence: Sequence::new(12),
            }),
            reference: None,
            risk: None,
        };
        let event = IntentEvent {
            intent_id: intent.intent_id.clone(),
            strategy_decision_id: intent.strategy_decision_id.clone(),
            event_sequence: Sequence::new(2),
            previous_status: Some(IntentStatus::Planned),
            status: IntentStatus::Executing,
            order_ids: vec![OrderId::new("order-1").unwrap()],
            completed_quantity: Quantity::new(1, 0).unwrap(),
            occurred_at_unix_nanos: UnixNanos::new(20),
            reason: "submitted child order".into(),
            dependency_watermarks: watermarks.clone(),
        };
        let state = IntentState {
            intent,
            status: IntentStatus::Executing,
            order_ids: event.order_ids.clone(),
            plan: None,
            completed_quantity: event.completed_quantity,
            updated_at_unix_nanos: event.occurred_at_unix_nanos,
            reason: event.reason.clone(),
            dependency_watermarks: watermarks,
            pending_orders: Vec::new(),
            dormant_orders: Vec::new(),
            pending_order_due_unix_nanos: BTreeMap::new(),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            pending_quote_refresh: None,
            compensation_attempts: 0,
        };
        let payloads = encode_business_change(
            "execution",
            &InstanceIdentity::new("workspace", "launch-1", "instance-1").unwrap(),
            9,
            20,
            0,
            &ExecutionBusinessChange::Intent {
                state: state.clone(),
                event: event.clone(),
            },
        )
        .unwrap();

        assert_eq!(payloads.len(), 1);
        let decoded = fb::root_as_intent_lifecycle_changed(&payloads[0]).unwrap();
        assert_eq!(decoded.previous_lifecycle(), fb::IntentLifecycle::PLANNED);
        assert_eq!(decoded.lifecycle(), fb::IntentLifecycle::EXECUTING);
        assert_eq!(
            decoded.intent().strategy_decision_id(),
            Some("strategy-a:decision:1")
        );
        assert_eq!(decoded.intent().execution_benchmarks().len(), 1);
        let benchmark = decoded.intent().execution_benchmarks().get(0);
        assert_eq!(benchmark.kind(), fb::ExecutionBenchmarkKind::ARRIVAL);
        assert_eq!(benchmark.instrument_id(), "BTC-USDT");
        assert_eq!(benchmark.market_id(), "market:test:BTC-USDT");
        assert_eq!(benchmark.price().mantissa(), 100);
        assert_eq!(benchmark.observed_at_unix_nanos(), 9);
        assert_eq!(decoded.dependency_evidence().len(), 2);
        assert_eq!(
            decoded.dependency_evidence().get(0).owner(),
            common_fb::DependencyOwner::ACCOUNT
        );
        assert_eq!(decoded.dependency_evidence().get(0).generation(), Some(7));
        assert_eq!(decoded.dependency_evidence().get(0).sequence(), Some(11));

        let mut rejected_state = state;
        rejected_state.status = IntentStatus::Rejected;
        rejected_state.reason = "child order rejected".into();
        let mut rejected_event = event;
        rejected_event.status = IntentStatus::Rejected;
        rejected_event.reason = rejected_state.reason.clone();
        let rejected_payloads = encode_business_change(
            "execution",
            &InstanceIdentity::new("workspace", "launch-1", "instance-1").unwrap(),
            10,
            21,
            0,
            &ExecutionBusinessChange::Intent {
                state: rejected_state,
                event: rejected_event,
            },
        )
        .unwrap();
        assert_eq!(rejected_payloads.len(), 1);
        let rejected = fb::root_as_intent_lifecycle_changed(&rejected_payloads[0]).unwrap();
        assert_eq!(rejected.previous_lifecycle(), fb::IntentLifecycle::PLANNED);
        assert_eq!(rejected.lifecycle(), fb::IntentLifecycle::REJECTED);
    }
}
