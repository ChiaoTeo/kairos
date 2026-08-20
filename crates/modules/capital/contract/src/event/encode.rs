use flatbuffers::FlatBufferBuilder;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2 as common_fb;

use super::CapitalEvent;
use crate::view::encode::{
    availability, capital_facts, demand, objective, operation, plan, policy, reservation, route,
};
use crate::{ContractError, ContractResult};

pub struct FlatbuffersCapitalEventWriter {
    actor_id: String,
    identity: InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersCapitalEventWriter {
    pub fn new(actor_id: impl Into<String>, identity: InstanceIdentity) -> Self {
        Self {
            actor_id: actor_id.into(),
            identity,
            last_payload: None,
        }
    }

    pub fn publish(&mut self, event: &CapitalEvent) -> Result<(), String> {
        let mut builder = FlatBufferBuilder::new();
        let metadata = event_metadata(
            &mut builder,
            &self.actor_id,
            &self.identity,
            event.sequence().get(),
            event.occurred_at().get(),
        );
        match event {
            CapitalEvent::FundingObjectiveChanged {
                objective: value, ..
            } => {
                let value = objective(&mut builder, value);
                let root = fb::FundingObjectiveChanged::create(
                    &mut builder,
                    &fb::FundingObjectiveChangedArgs {
                        metadata: Some(metadata),
                        objective: Some(value),
                    },
                );
                fb::finish_funding_objective_changed_buffer(&mut builder, root);
            },
            CapitalEvent::CapitalDemandChanged { demand: value, .. } => {
                let value = demand(&mut builder, value);
                let root = fb::CapitalDemandChanged::create(
                    &mut builder,
                    &fb::CapitalDemandChangedArgs {
                        metadata: Some(metadata),
                        demand: Some(value),
                    },
                );
                fb::finish_capital_demand_changed_buffer(&mut builder, root);
            },
            CapitalEvent::PolicyChanged { policy: value, .. } => {
                let value = policy(&mut builder, value);
                let root = fb::CapitalPolicyChanged::create(
                    &mut builder,
                    &fb::CapitalPolicyChangedArgs {
                        metadata: Some(metadata),
                        policy: Some(value),
                    },
                );
                fb::finish_capital_policy_changed_buffer(&mut builder, root);
            },
            CapitalEvent::FactsObserved { facts: value, .. } => {
                let value = capital_facts(&mut builder, value);
                let root = fb::CapitalFactsObserved::create(
                    &mut builder,
                    &fb::CapitalFactsObservedArgs {
                        metadata: Some(metadata),
                        facts: Some(value),
                    },
                );
                fb::finish_capital_facts_observed_buffer(&mut builder, root);
            },
            CapitalEvent::AvailabilityEvaluated {
                availability: values,
                ..
            } => {
                let values = values
                    .iter()
                    .map(|value| availability(&mut builder, value))
                    .collect::<Vec<_>>();
                let values = builder.create_vector(&values);
                let root = fb::CapitalAvailabilityEvaluated::create(
                    &mut builder,
                    &fb::CapitalAvailabilityEvaluatedArgs {
                        metadata: Some(metadata),
                        availability: Some(values),
                    },
                );
                fb::finish_capital_availability_evaluated_buffer(&mut builder, root);
            },
            CapitalEvent::RouteChanged { route: value, .. } => {
                let value = route(&mut builder, value);
                let root = fb::CapitalRouteChanged::create(
                    &mut builder,
                    &fb::CapitalRouteChangedArgs {
                        metadata: Some(metadata),
                        route: Some(value),
                    },
                );
                fb::finish_capital_route_changed_buffer(&mut builder, root);
            },
            CapitalEvent::PlanAuthorized {
                plan: plan_value,
                reservation: reservation_value,
                ..
            } => {
                let plan_value = plan(&mut builder, plan_value);
                let reservation_value = reservation(&mut builder, reservation_value);
                let root = fb::CapitalPlanAuthorized::create(
                    &mut builder,
                    &fb::CapitalPlanAuthorizedArgs {
                        metadata: Some(metadata),
                        plan: Some(plan_value),
                        reservation: Some(reservation_value),
                    },
                );
                fb::finish_capital_plan_authorized_buffer(&mut builder, root);
            },
            CapitalEvent::PlanStateChanged {
                plan: plan_value,
                reservation: reservation_value,
                operation: operation_value,
                ..
            } => {
                let plan_value = plan(&mut builder, plan_value);
                let reservation_value = reservation(&mut builder, reservation_value);
                let operation_value = operation(&mut builder, operation_value);
                let root = fb::CapitalPlanStateChanged::create(
                    &mut builder,
                    &fb::CapitalPlanStateChangedArgs {
                        metadata: Some(metadata),
                        plan: Some(plan_value),
                        reservation: Some(reservation_value),
                        operation: Some(operation_value),
                    },
                );
                fb::finish_capital_plan_state_changed_buffer(&mut builder, root);
            },
            CapitalEvent::PlanExpired {
                plan: plan_value,
                reservation: reservation_value,
                operation: operation_value,
                ..
            } => {
                let plan_value = plan(&mut builder, plan_value);
                let reservation_value = reservation(&mut builder, reservation_value);
                let operation_value = operation_value
                    .as_ref()
                    .map(|value| operation(&mut builder, value));
                let root = fb::CapitalPlanExpired::create(
                    &mut builder,
                    &fb::CapitalPlanExpiredArgs {
                        metadata: Some(metadata),
                        plan: Some(plan_value),
                        reservation: Some(reservation_value),
                        operation: operation_value,
                    },
                );
                fb::finish_capital_plan_expired_buffer(&mut builder, root);
            },
        }
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}

pub struct CapitalAeronEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    encoder: FlatbuffersCapitalEventWriter,
}

impl CapitalAeronEventPublisher {
    pub fn connect(
        endpoint: &kairos_transport::AeronEndpoint,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> ContractResult<Self> {
        if endpoint.stream_id() != kairos_transport::stream_ids::CAPITAL_EVENTS {
            return Err(ContractError::Invalid(format!(
                "Capital events require stream id {}, received {}",
                kairos_transport::stream_ids::CAPITAL_EVENTS,
                endpoint.stream_id()
            )));
        }
        Ok(Self {
            publisher: kairos_transport::AeronBytePublisher::connect_endpoint(endpoint)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            encoder: FlatbuffersCapitalEventWriter::new(actor_id, identity),
        })
    }

    pub fn publish(&mut self, event: &CapitalEvent) -> ContractResult<()> {
        self.encoder
            .publish(event)
            .map_err(ContractError::Invalid)?;
        self.publisher
            .publish(self.encoder.last_payload.as_deref().unwrap_or_default())
            .map(|_| ())
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

fn event_metadata<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    occurred_at: u64,
) -> flatbuffers::WIPOffset<common_fb::EventMetadata<'a>> {
    let event_id = builder.create_string(&format!("capital:{actor_id}:{sequence}"));
    let stream_id = builder.create_string("capital.events");
    let producer_id = builder.create_string(actor_id);
    let workspace_id = builder.create_string(identity.workspace_id.as_str());
    let launch_id = identity
        .launch_id()
        .map(|value| builder.create_string(value.as_str()));
    let instance_id = identity
        .instance_id()
        .map(|value| builder.create_string(value.as_str()));
    common_fb::EventMetadata::create(
        builder,
        &common_fb::EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            sequence,
            producer_id: Some(producer_id),
            workspace_id: Some(workspace_id),
            launch_id,
            instance_id,
            correlation_id: None,
            causation_id: None,
            occurred_at_unix_nanos: occurred_at,
            published_at_unix_nanos: super::super::view::encode::now_unix_nanos(),
        },
    )
}

#[cfg(test)]
mod tests {
    use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
    use kairos_primitives::reference::Currency;
    use kairos_primitives::time::{Generation, Sequence, UnixNanos};

    use super::*;
    use crate::{CapitalPolicy, FundingLocation};

    #[test]
    fn policy_event_is_a_typed_flatbuffer_with_stable_sequence() {
        let mut writer = FlatbuffersCapitalEventWriter::new(
            "capital:group-1",
            InstanceIdentity::new("workspace", "launch", "instance").unwrap(),
        );
        writer
            .publish(&CapitalEvent::PolicyChanged {
                policy: CapitalPolicy {
                    destination: FundingLocation {
                        broker: BrokerId::new("binance").unwrap(),
                        account_id: AccountId::new("account-1").unwrap(),
                        segment: SegmentKey::new("usd-m").unwrap(),
                        asset: Currency::new("USDT").unwrap(),
                    },
                    version: Generation::new(1),
                    minimum: "10".parse().unwrap(),
                    default_target: "20".parse().unwrap(),
                    maximum: "30".parse().unwrap(),
                    stress_buffer: "2".parse().unwrap(),
                    minimum_movement: "1".parse().unwrap(),
                    hysteresis: "1".parse().unwrap(),
                    deficit_dwell_nanos: 5.into(),
                    cooldown_nanos: 6.into(),
                    max_fact_age_nanos: 7.into(),
                },
                event_sequence: Sequence::new(11),
                occurred_at: UnixNanos::new(12),
            })
            .unwrap();
        let payload = writer.last_payload.unwrap();
        assert!(fb::capital_policy_changed_buffer_has_identifier(&payload));
        let root = fb::root_as_capital_policy_changed(&payload).unwrap();
        assert_eq!(root.metadata().sequence(), 11);
        assert_eq!(root.policy().destination().account_id(), "account-1");
    }
}
