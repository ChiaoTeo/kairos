use crate::application::protocol::{RiskEventSink, RiskPublisher};
use crate::application::{RiskEvent, RiskSnapshot};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    common::v_1::{
        Decimal64, MessageHeader, MessageHeaderArgs, SnapshotHeader, SnapshotHeaderArgs,
    },
    risk::v_1 as risk_fb,
};

pub struct FlatbuffersRiskPublisher {
    pub actor_id: String,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskPublisher {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            last_payload: None,
        }
    }
}

impl RiskPublisher for FlatbuffersRiskPublisher {
    fn publish(&mut self, snapshot: &RiskSnapshot) -> Result<(), String> {
        let mut builder = FlatBufferBuilder::new();
        let mut budgets = Vec::new();
        for budget in &snapshot.budgets {
            let budget_id = builder.create_string(&budget.budget_id);
            let owner_id = builder.create_string(&budget.owner_id);
            let metric = builder.create_string(budget.metric.as_str());
            let status = builder.create_string("active");
            let limit = Decimal64::new(budget.limit.mantissa, budget.limit.scale);
            let used = Decimal64::new(budget.used.mantissa, budget.used.scale);
            let reserved = Decimal64::new(budget.reserved.mantissa, budget.reserved.scale);
            let available = budget.available();
            let available = Decimal64::new(available.mantissa, available.scale);
            budgets.push(risk_fb::Budget::create(
                &mut builder,
                &risk_fb::BudgetArgs {
                    budget_id: Some(budget_id),
                    owner_id: Some(owner_id),
                    metric: Some(metric),
                    limit: Some(&limit),
                    used: Some(&used),
                    reserved: Some(&reserved),
                    available: Some(&available),
                    status: Some(status),
                    ..Default::default()
                },
            ));
        }
        let budgets = builder.create_vector(&budgets);
        let mut reservation_offsets = Vec::new();
        for reservation in &snapshot.reservations {
            let allocation = reservation
                .allocations
                .first()
                .ok_or_else(|| "reservation has no allocation".to_string())?;
            let reservation_id = builder.create_string(&reservation.reservation_id);
            let owner_id = builder.create_string(&snapshot.actor_id);
            let metric = builder.create_string(allocation.metric.as_str());
            let status = builder.create_string(match reservation.status {
                crate::domain::ReservationStatus::Reserved => "reserved",
                crate::domain::ReservationStatus::Consumed => "consumed",
                crate::domain::ReservationStatus::Released => "released",
                crate::domain::ReservationStatus::Expired => "expired",
            });
            let amount = Decimal64::new(allocation.amount.mantissa, allocation.amount.scale);
            let mut allocation_offsets = Vec::new();
            for item in &reservation.allocations {
                let budget_id = builder.create_string(&item.budget_id);
                let metric = builder.create_string(item.metric.as_str());
                let amount = Decimal64::new(item.amount.mantissa, item.amount.scale);
                allocation_offsets.push(risk_fb::Allocation::create(
                    &mut builder,
                    &risk_fb::AllocationArgs {
                        budget_id: Some(budget_id),
                        metric: Some(metric),
                        amount: Some(&amount),
                    },
                ));
            }
            let allocations = builder.create_vector(&allocation_offsets);
            reservation_offsets.push(risk_fb::Reservation::create(
                &mut builder,
                &risk_fb::ReservationArgs {
                    reservation_id: Some(reservation_id),
                    owner_id: Some(owner_id),
                    metric: Some(metric),
                    amount: Some(&amount),
                    allocations: Some(allocations),
                    status: Some(status),
                    created_at_unix_nanos: reservation.created_at_unix_nanos,
                    updated_at_unix_nanos: reservation.updated_at_unix_nanos,
                    ..Default::default()
                },
            ));
        }
        let reservations = builder.create_vector(&reservation_offsets);
        let payload = risk_fb::Risk::create(
            &mut builder,
            &risk_fb::RiskArgs {
                budget_count: snapshot.budgets.len() as u64,
                reservation_count: snapshot.reservations.len() as u64,
                budgets: Some(budgets),
                reservations: Some(reservations),
            },
        );
        let snapshot_id = builder.create_string(&format!("risk-{}", snapshot.event_sequence));
        let view_key = builder.create_string("risk.budgets");
        let owner = builder.create_string(&self.actor_id);
        let stream = builder.create_string("risk.events");
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(owner),
                event_stream_id: Some(stream),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                event_sequence: snapshot.event_sequence,
                version: 1,
                generation: snapshot.generation,
                generated_at_unix_nanos: 0,
                as_of_unix_nanos: 0,
                complete: true,
            },
        );
        let root = risk_fb::RiskSnapshot::create(
            &mut builder,
            &risk_fb::RiskSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        risk_fb::finish_risk_snapshot_buffer(&mut builder, root);
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}

pub struct FlatbuffersRiskEventPublisher {
    pub actor_id: String,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskEventPublisher {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            last_payload: None,
        }
    }
}

impl RiskEventSink for FlatbuffersRiskEventPublisher {
    fn publish(&mut self, event: &RiskEvent) -> Result<(), String> {
        let RiskEvent::ReservationChanged {
            reservation,
            event_sequence,
        } = event;
        let mut builder = FlatBufferBuilder::new();
        let message_id = builder.create_string(&format!("risk-reservation-{}", event_sequence));
        let stream_id = builder.create_string("risk.events");
        let producer_id = builder.create_string(&self.actor_id);
        let header = MessageHeader::create(
            &mut builder,
            &MessageHeaderArgs {
                message_id: Some(message_id),
                stream_id: Some(stream_id),
                producer_id: Some(producer_id),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                sequence: *event_sequence,
                event_time_unix_nanos: reservation.updated_at_unix_nanos,
                publish_time_unix_nanos: reservation.updated_at_unix_nanos,
            },
        );
        let reservation_id = builder.create_string(&reservation.reservation_id);
        let request_id = builder.create_string(&reservation.request_id);
        let status = builder.create_string(match reservation.status {
            crate::domain::ReservationStatus::Reserved => "reserved",
            crate::domain::ReservationStatus::Consumed => "consumed",
            crate::domain::ReservationStatus::Released => "released",
            crate::domain::ReservationStatus::Expired => "expired",
        });
        let mut allocation_offsets = Vec::new();
        for allocation in &reservation.allocations {
            let budget_id = builder.create_string(&allocation.budget_id);
            let metric = builder.create_string(allocation.metric.as_str());
            let amount = Decimal64::new(allocation.amount.mantissa, allocation.amount.scale);
            allocation_offsets.push(risk_fb::Allocation::create(
                &mut builder,
                &risk_fb::AllocationArgs {
                    budget_id: Some(budget_id),
                    metric: Some(metric),
                    amount: Some(&amount),
                },
            ));
        }
        let allocations = builder.create_vector(&allocation_offsets);
        let root = risk_fb::ReservationEvent::create(
            &mut builder,
            &risk_fb::ReservationEventArgs {
                header: Some(header),
                reservation_id: Some(reservation_id),
                request_id: Some(request_id),
                status: Some(status),
                allocations: Some(allocations),
                occurred_at_unix_nanos: reservation.updated_at_unix_nanos,
            },
        );
        risk_fb::finish_reservation_event_buffer(&mut builder, root);
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}
