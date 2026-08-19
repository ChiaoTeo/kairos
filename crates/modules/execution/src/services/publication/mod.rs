//! Concrete Execution v2 publication services.
//!
//! The application layer still owns business values.  This module is the
//! composition boundary where those values will be encoded into the v2
//! contract and sent over Aeron or mmap.  Keeping the adapters here prevents
//! generated FlatBuffers types from leaking into the Actor.

use crate::application::ExecutionCurrentView;
use crate::domain::{
    CommitmentBasis, CommitmentResource, CommitmentStatus, ExecutionOrder, ExecutionOrderStatus,
    OrderCommitment, OrderSide, OrderType, RiskReservationEvidence, RiskReservationSagaStatus,
};
use flatbuffers::FlatBufferBuilder;
use kairos_execution_contract::{event_metadata, view_metadata, EncodeContext, ExecutionViewKey};
use kairos_protocol::generated::kairos::common::v_2 as common_fb;
use kairos_protocol::generated::kairos::execution::v_2 as fb;
use kairos_protocol::InstanceIdentity;

mod encoding;
mod events;

pub(crate) use encoding::*;
pub(crate) use events::encode_business_change;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::{
        DependencyWatermarks, ExecuteStrategyIntent, ExecutionBusinessChange, IntentEvent,
        IntentState, IntentStatus, SnapshotWatermark,
    };
    use crate::domain::{
        CommitmentBasis, CommitmentResource, OrderCommitment, RiskReservationEvidence,
        RiskReservationSagaStatus,
    };
    use kairos_execution_contract::ExecutionViewKind;
    use kairos_primitives::{
        AccountId, Currency, Generation, InstrumentId, Money, OrderId, Quantity, SegmentKey,
        Sequence, UnixNanos,
    };
    use std::collections::BTreeMap;

    #[test]
    fn active_orders_mmap_encodes_commitments_and_risk_saga() {
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
            CommitmentResource::Asset(Currency::new("USDT").unwrap()),
            Money::new(100, 0).unwrap(),
            Quantity::new(1, 0).unwrap(),
            CommitmentBasis::QuotePriceCap {
                price_cap: kairos_primitives::Price::new(100, 0).unwrap(),
            },
            UnixNanos::new(10),
        )
        .unwrap();
        commitment.settlement_asset = Some(Currency::new("USDT").unwrap());
        let snapshot = ExecutionCurrentView {
            generation: Generation::new(3),
            event_sequence: Sequence::new(4),
            orders: Vec::new(),
            commitments: vec![commitment],
            risk_reservations: vec![RiskReservationEvidence {
                order_id,
                reservation_id: "execution:order-1".into(),
                idempotency_key: "execution:order-1".into(),
                account_id,
                amount: Money::new(100, 0).unwrap(),
                status: RiskReservationSagaStatus::Active,
                risk_generation: 7,
                risk_event_sequence: 8,
                policy_version: 2,
                expires_at_unix_nanos: UnixNanos::new(1000),
                updated_at_unix_nanos: UnixNanos::new(11),
                funding_requirement: None,
            }],
            intents: Vec::new(),
            events: Vec::new(),
            intent_events: Vec::new(),
            fills: Vec::new(),
            unknown_remote_orders: Vec::new(),
            exchange_event_watermark_unix_nanos: UnixNanos::new(0),
        };
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let key = ExecutionViewKey::new(
            identity.workspace_id.clone(),
            ExecutionViewKind::ActiveOrders,
            Some(identity.launch_id.clone()),
            Some(identity.instance_id.clone()),
        )
        .unwrap();
        let bytes = encode_active_orders("execution", &identity, 3, &key, &snapshot).unwrap();
        let decoded = fb::root_as_active_orders_view(&bytes).unwrap();
        assert_eq!(decoded.commitments().len(), 1);
        assert_eq!(decoded.risk_reservations().len(), 1);
        assert_eq!(
            decoded.commitments().get(0).lifecycle(),
            fb::CommitmentLifecycle::HELD_BEFORE_SEND
        );
        assert_eq!(
            decoded.commitments().get(0).settlement_asset(),
            Some("USDT")
        );
        assert_eq!(
            decoded.risk_reservations().get(0).lifecycle(),
            fb::RiskReservationSagaLifecycle::ACTIVE
        );

        let current_key = ExecutionViewKey::new(
            identity.workspace_id.clone(),
            ExecutionViewKind::CurrentExecution,
            Some(identity.launch_id.clone()),
            Some(identity.instance_id.clone()),
        )
        .unwrap();
        let bytes =
            encode_current_execution("execution", &identity, 3, &current_key, &snapshot).unwrap();
        let current = fb::root_as_current_execution_view(&bytes).unwrap();
        assert_eq!(current.metadata().generation(), 3);
        assert_eq!(current.metadata().applied_revision(), Some(4));
        assert_eq!(current.commitments().len(), 1);
        assert_eq!(current.risk_reservations().len(), 1);
    }

    #[test]
    fn lifecycle_event_preserves_correlation_previous_state_and_evidence() {
        let mut intent = ExecuteStrategyIntent::default();
        intent.intent_id = kairos_primitives::IntentId::new("intent-1").unwrap();
        intent.strategy_id = "strategy-a".into();
        intent.strategy_decision_id = Some("strategy-a:decision:1".into());
        intent.launch_id = "launch-1".into();
        intent.instance_id = "instance-1".into();
        intent.account_ids = vec![AccountId::new("main").unwrap()];
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
            pending_order_due_unix_nanos: BTreeMap::new(),
            quote_version: 0,
            last_quote_refresh_unix_nanos: None,
            compensation_attempts: 0,
        };
        let payloads = encode_business_change(
            "execution",
            &InstanceIdentity::new("workspace", "launch-1", "instance-1"),
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
            &InstanceIdentity::new("workspace", "launch-1", "instance-1"),
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
