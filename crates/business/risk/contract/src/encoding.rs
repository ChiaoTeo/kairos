use crate::model::{ReservationStatus, RiskCurrentView, RiskEvent};
use crate::transport::MmapSnapshotPublisher;
use crate::{ContractError, ContractResult, SnapshotEnvelope};
use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    common::v_1::{
        Decimal64, MessageHeader, MessageHeaderArgs, SnapshotHeader, SnapshotHeaderArgs,
    },
    risk::v_1 as risk_fb,
};

pub struct FlatbuffersRiskSnapshotWriter {
    pub actor_id: String,
    pub last_payload: Option<Vec<u8>>,
}

/// Publishes the encoded Risk snapshot through the stable mmap contract.
pub struct MmapRiskSnapshotPublisher {
    publisher: MmapSnapshotPublisher,
    encoder: FlatbuffersRiskSnapshotWriter,
}

impl MmapRiskSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> ContractResult<Self> {
        let actor_id = actor_id.into();
        Ok(Self {
            publisher: MmapSnapshotPublisher::create(path, slot_size)?,
            encoder: FlatbuffersRiskSnapshotWriter::new(actor_id),
        })
    }

    pub fn publish(&mut self, snapshot: &RiskCurrentView) -> ContractResult<()> {
        self.encoder
            .publish(snapshot)
            .map_err(ContractError::Invalid)?;
        self.publisher.publish(&SnapshotEnvelope {
            view_key: "risk.budgets".into(),
            producer_id: self.encoder.actor_id.clone(),
            generation: snapshot.generation,
            published_at_unix_nanos: 0,
            payload: self.encoder.last_payload.clone().unwrap_or_default(),
        })
    }
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            last_payload: None,
        }
    }
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn publish(&mut self, snapshot: &RiskCurrentView) -> Result<(), String> {
        let mut builder = FlatBufferBuilder::new();
        let mut budgets = Vec::new();
        for limit in &snapshot.limits {
            let budget_id = builder.create_string(&limit.policy.policy_id);
            let owner_id = builder.create_string(
                limit
                    .policy
                    .scope
                    .account_id
                    .as_deref()
                    .or(limit.policy.scope.strategy_id.as_deref())
                    .or(limit.policy.scope.instrument_id.as_deref())
                    .or(limit.policy.scope.exchange_id.as_deref())
                    .unwrap_or(&snapshot.actor_id),
            );
            let metric = builder.create_string(limit.policy.metric.as_str());
            let status = builder.create_string("active");
            let limit_value = Decimal64::new(limit.policy.limit.mantissa, limit.policy.limit.scale);
            let used = Decimal64::new(limit.used.mantissa, limit.used.scale);
            let reserved = Decimal64::new(limit.reserved.mantissa, limit.reserved.scale);
            let available = Decimal64::new(limit.available.mantissa, limit.available.scale);
            budgets.push(risk_fb::Budget::create(
                &mut builder,
                &risk_fb::BudgetArgs {
                    budget_id: Some(budget_id),
                    owner_id: Some(owner_id),
                    metric: Some(metric),
                    limit: Some(&limit_value),
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
                ReservationStatus::Reserved => "reserved",
                ReservationStatus::Consumed => "consumed",
                ReservationStatus::Released => "released",
                ReservationStatus::Expired => "expired",
            });
            let amount = Decimal64::new(allocation.amount.mantissa, allocation.amount.scale);
            let mut allocation_offsets = Vec::new();
            for item in &reservation.allocations {
                let budget_id = builder.create_string(&item.policy_id);
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
        let mut circuit_offsets = Vec::new();
        for circuit in &snapshot.circuits {
            let account_id = circuit
                .scope
                .account_id
                .as_deref()
                .map(|value| builder.create_string(value));
            let strategy_id = circuit
                .scope
                .strategy_id
                .as_deref()
                .map(|value| builder.create_string(value));
            let exchange_id = circuit
                .scope
                .exchange_id
                .as_deref()
                .map(|value| builder.create_string(value));
            let state = builder.create_string(if circuit.open { "open" } else { "closed" });
            let reason = builder.create_string(&circuit.reason);
            circuit_offsets.push(risk_fb::CircuitState::create(
                &mut builder,
                &risk_fb::CircuitStateArgs {
                    account_id,
                    strategy_id,
                    exchange_id,
                    state: Some(state),
                    opened_at_unix_nanos: circuit.opened_at_unix_nanos.unwrap_or_default(),
                    reset_at_unix_nanos: circuit.reset_at_unix_nanos.unwrap_or_default(),
                    reason: Some(reason),
                },
            ));
        }
        let circuits = builder.create_vector(&circuit_offsets);
        let payload = risk_fb::Risk::create(
            &mut builder,
            &risk_fb::RiskArgs {
                budget_count: snapshot.limits.len() as u64,
                reservation_count: snapshot.reservations.len() as u64,
                circuit_count: snapshot.circuits.len() as u64,
                budgets: Some(budgets),
                reservations: Some(reservations),
                circuits: Some(circuits),
            },
        );
        let snapshot_id = builder.create_string(&format!("risk-{}", snapshot.generation));
        let view_key = builder.create_string("risk.budgets");
        let owner = builder.create_string(&self.actor_id);
        let header = SnapshotHeader::create(
            &mut builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view_key),
                owner_actor_id: Some(owner),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
                version: 1,
                generation: snapshot.generation,
                generated_at_unix_nanos: now_unix_nanos(),
                as_of_unix_nanos: risk_snapshot_as_of(snapshot),
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

fn risk_snapshot_as_of(snapshot: &RiskCurrentView) -> u64 {
    let policy_time = snapshot
        .limits
        .iter()
        .map(|limit| limit.policy.valid_from_unix_nanos)
        .max()
        .unwrap_or_default();
    let reservation_time = snapshot
        .reservations
        .iter()
        .map(|reservation| reservation.updated_at_unix_nanos)
        .max()
        .unwrap_or_default();
    let circuit_time = snapshot
        .circuits
        .iter()
        .flat_map(|circuit| [circuit.opened_at_unix_nanos, circuit.reset_at_unix_nanos])
        .flatten()
        .max()
        .unwrap_or_default();
    policy_time.max(reservation_time).max(circuit_time)
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

pub struct FlatbuffersRiskEventWriter {
    pub actor_id: String,
    identity: kairos_protocol::InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
}

pub struct RiskAeronEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    encoder: FlatbuffersRiskEventWriter,
}

impl RiskAeronEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> ContractResult<Self> {
        Ok(Self {
            publisher: kairos_transport::AeronBytePublisher::connect(aeron_dir, channel, stream_id)
                .map_err(ContractError::Transport)?,
            encoder: FlatbuffersRiskEventWriter::new_with_identity(actor_id, identity),
        })
    }

    pub fn publish(&mut self, event: &RiskEvent) -> ContractResult<()> {
        self.encoder
            .publish(event)
            .map_err(ContractError::Invalid)?;
        self.publisher
            .publish(self.encoder.last_payload.as_deref().unwrap_or_default())
            .map_err(ContractError::Transport)
    }
}

impl FlatbuffersRiskEventWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(actor_id, kairos_protocol::InstanceIdentity::default())
    }

    pub fn new_with_identity(
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            identity,
            last_payload: None,
        }
    }
}

impl FlatbuffersRiskEventWriter {
    pub fn publish(&mut self, event: &RiskEvent) -> Result<(), String> {
        let (kind_value, event_sequence, occurred_at, account_value, strategy_value) = match event {
            RiskEvent::ReservationChanged {
                reservation,
                event_sequence,
            } => (
                "reservation_changed",
                *event_sequence,
                reservation.updated_at_unix_nanos,
                reservation.account_id.as_deref(),
                reservation.strategy_id.as_deref(),
            ),
            RiskEvent::DecisionEvaluated {
                decision,
                account_id,
                strategy_id,
                event_sequence,
            } => (
                "decision_evaluated",
                *event_sequence,
                decision.evaluated_at_unix_nanos,
                Some(account_id.as_str()),
                Some(strategy_id.as_str()),
            ),
            RiskEvent::CircuitChanged {
                circuit,
                event_sequence,
            } => (
                "circuit_changed",
                *event_sequence,
                circuit
                    .reset_at_unix_nanos
                    .or(circuit.opened_at_unix_nanos)
                    .unwrap_or_default(),
                circuit.scope.account_id.as_deref(),
                circuit.scope.strategy_id.as_deref(),
            ),
            RiskEvent::PolicyActivated {
                policy,
                event_sequence,
            } => (
                "policy_activated",
                *event_sequence,
                policy.valid_from_unix_nanos,
                policy.scope.account_id.as_deref(),
                policy.scope.strategy_id.as_deref(),
            ),
        };
        let mut builder = FlatBufferBuilder::new();
        let message_id = builder.create_string(&format!("risk:{event_sequence}"));
        let stream_id = builder.create_string("risk.events");
        let producer_id = builder.create_string(&self.actor_id);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let header = MessageHeader::create(
            &mut builder,
            &MessageHeaderArgs {
                message_id: Some(message_id),
                stream_id: Some(stream_id),
                producer_id: Some(producer_id),
                workspace_id,
                launch_id,
                instance_id,
                sequence: event_sequence,
                event_time_unix_nanos: occurred_at,
                publish_time_unix_nanos: occurred_at,
            },
        );
        let kind = builder.create_string(kind_value);
        let account_id = account_value.map(|value| builder.create_string(value));
        let strategy_id = strategy_value.map(|value| builder.create_string(value));

        let (decision_id, allowed, degraded, reason_values, violation_values) =
            if let RiskEvent::DecisionEvaluated { decision, .. } = event {
                (
                    Some(builder.create_string(&decision.decision_id)),
                    decision.allowed,
                    decision.degraded,
                    decision
                        .reason_codes
                        .iter()
                        .map(reason_code_name)
                        .collect::<Vec<_>>(),
                    decision
                        .violations
                        .iter()
                        .map(String::as_str)
                        .collect::<Vec<_>>(),
                )
            } else {
                (None, false, false, Vec::new(), Vec::new())
            };
        let request_id = match event {
            RiskEvent::DecisionEvaluated { decision, .. } => {
                Some(builder.create_string(&decision.request_id))
            }
            RiskEvent::ReservationChanged { reservation, .. } => {
                Some(builder.create_string(&reservation.request_id))
            }
            RiskEvent::PolicyActivated { .. } | RiskEvent::CircuitChanged { .. } => None,
        };
        let reason_offsets = reason_values
            .iter()
            .map(|value| builder.create_string(value))
            .collect::<Vec<_>>();
        let violation_offsets = violation_values
            .iter()
            .map(|value| builder.create_string(value))
            .collect::<Vec<_>>();
        let reason_codes =
            (!reason_offsets.is_empty()).then(|| builder.create_vector(&reason_offsets));
        let violations =
            (!violation_offsets.is_empty()).then(|| builder.create_vector(&violation_offsets));

        let (reservation_id, reservation_status) =
            if let RiskEvent::ReservationChanged { reservation, .. } = event {
                (
                    Some(builder.create_string(&reservation.reservation_id)),
                    Some(builder.create_string(match reservation.status {
                        ReservationStatus::Reserved => "reserved",
                        ReservationStatus::Consumed => "consumed",
                        ReservationStatus::Released => "released",
                        ReservationStatus::Expired => "expired",
                    })),
                )
            } else {
                (None, None)
            };

        let circuit = if let RiskEvent::CircuitChanged { circuit, .. } = event {
            let exchange_id = circuit
                .scope
                .exchange_id
                .as_deref()
                .map(|value| builder.create_string(value));
            let state = builder.create_string(if circuit.open { "open" } else { "closed" });
            let reason = builder.create_string(&circuit.reason);
            Some(risk_fb::CircuitState::create(
                &mut builder,
                &risk_fb::CircuitStateArgs {
                    account_id,
                    strategy_id,
                    exchange_id,
                    state: Some(state),
                    opened_at_unix_nanos: circuit.opened_at_unix_nanos.unwrap_or_default(),
                    reset_at_unix_nanos: circuit.reset_at_unix_nanos.unwrap_or_default(),
                    reason: Some(reason),
                },
            ))
        } else {
            None
        };
        let root = risk_fb::RiskEventMessage::create(
            &mut builder,
            &risk_fb::RiskEventMessageArgs {
                header: Some(header),
                kind: Some(kind),
                account_id,
                strategy_id,
                decision_id,
                request_id,
                allowed,
                degraded,
                reason_codes,
                violations,
                reservation_id,
                reservation_status,
                circuit,
                occurred_at_unix_nanos: occurred_at,
            },
        );
        risk_fb::finish_risk_event_message_buffer(&mut builder, root);
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}

fn reason_code_name(value: &crate::model::ReasonCode) -> &'static str {
    use crate::model::ReasonCode;
    match value {
        ReasonCode::NoMatchingPolicy => "no_matching_policy",
        ReasonCode::LimitExceeded => "limit_exceeded",
        ReasonCode::StaleDependency => "stale_dependency",
        ReasonCode::DuplicateRequest => "duplicate_request",
        ReasonCode::ReservationNotFound => "reservation_not_found",
        ReasonCode::ReservationNotActive => "reservation_not_active",
        ReasonCode::InvalidRequest => "invalid_request",
        ReasonCode::PersistenceFailure => "persistence_failure",
        ReasonCode::CircuitOpen => "circuit_open",
        ReasonCode::StaleMarket => "stale_market",
        ReasonCode::InsufficientMargin => "insufficient_margin",
        ReasonCode::LeverageExceeded => "leverage_exceeded",
        ReasonCode::LossLimitExceeded => "loss_limit_exceeded",
    }
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{CircuitScope, CircuitState};

    #[test]
    fn snapshot_header_carries_generation_and_business_as_of_time() {
        let snapshot = RiskCurrentView {
            actor_id: "risk:test".into(),
            generation: 11,
            policy_version: 1,
            limits: vec![],
            reservations: vec![],
            circuits: vec![CircuitState {
                scope: CircuitScope {
                    account_id: None,
                    strategy_id: None,
                    exchange_id: None,
                },
                open: true,
                opened_at_unix_nanos: Some(789),
                reset_at_unix_nanos: None,
                reason: "test".into(),
            }],
        };
        let mut writer = FlatbuffersRiskSnapshotWriter::new("risk:test");

        writer.publish(&snapshot).unwrap();
        let root = risk_fb::root_as_risk_snapshot(writer.last_payload.as_deref().unwrap()).unwrap();
        let header = root.header();
        assert_eq!(header.version(), 1);
        assert_eq!(header.generation(), 11);
        assert_eq!(header.as_of_unix_nanos(), 789);
        assert!(header.generated_at_unix_nanos() > 0);
        assert!(header.complete());
    }
}
