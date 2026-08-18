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
use kairos_protocol::generated::kairos::execution::v_2 as fb;
use kairos_protocol::InstanceIdentity;

mod encoding;
mod events;

pub(crate) use encoding::*;
pub(crate) use events::encode_business_change;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{
        CommitmentBasis, CommitmentResource, OrderCommitment, RiskReservationEvidence,
        RiskReservationSagaStatus,
    };
    use kairos_execution_contract::ExecutionViewKind;
    use kairos_primitives::{
        AccountId, Currency, Generation, InstrumentId, Money, OrderId, Quantity, SegmentKey,
        Sequence, UnixNanos,
    };

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
}
