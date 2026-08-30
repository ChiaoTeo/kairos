//! FlatBuffers v2 encoders for the Risk boundary.
//!
//! Current state is `RXV2`; durable facts use one typed root per fact.

use std::collections::BTreeMap;

use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_2::{self as common_fb, Decimal64};
use kairos_protocol::generated::kairos::risk::v_2 as fb;

use crate::control::{ReservationStatus, RiskCurrentView, RiskDecision, RiskEvent};

pub fn encode_indexed_current(
    snapshot: &RiskCurrentView,
) -> Result<BTreeMap<(String, Vec<u8>), Vec<u8>>, String> {
    let mut values = BTreeMap::new();
    let actor_id = snapshot.actor_id.as_str();
    insert_indexed(&mut values, crate::RISK_STATE_DATABASE, &[actor_id], |b| {
        let actor_id = b.create_string(actor_id);
        let root = fb::RiskStateCurrent::create(
            b,
            &fb::RiskStateCurrentArgs {
                actor_id: Some(actor_id),
                generation: snapshot.generation.get(),
                policy_version: snapshot.policy_version.get(),
            },
        );
        fb::finish_risk_state_current_buffer(b, root);
        Ok(())
    })?;
    for limit_view in &snapshot.limits {
        let policy_id = limit_view.policy.policy_id.as_str();
        insert_indexed(
            &mut values,
            crate::RISK_POLICIES_DATABASE,
            &[policy_id],
            |b| {
                let actor_id = b.create_string(actor_id);
                let policy = policy(b, &limit_view.policy);
                let root = fb::RiskPolicyCurrent::create(
                    b,
                    &fb::RiskPolicyCurrentArgs {
                        actor_id: Some(actor_id),
                        policy: Some(policy),
                    },
                );
                fb::finish_risk_policy_current_buffer(b, root);
                Ok(())
            },
        )?;
        insert_indexed(
            &mut values,
            crate::RISK_LIMIT_USAGE_DATABASE,
            &[policy_id],
            |b| {
                let actor_id = b.create_string(actor_id);
                let policy_id = b.create_string(policy_id);
                let used = decimal(limit_view.used);
                let reserved = decimal(limit_view.reserved);
                let available = decimal(limit_view.available);
                let root = fb::RiskLimitUsageCurrent::create(
                    b,
                    &fb::RiskLimitUsageCurrentArgs {
                        actor_id: Some(actor_id),
                        policy_id: Some(policy_id),
                        used: Some(&used),
                        reserved: Some(&reserved),
                        available: Some(&available),
                    },
                );
                fb::finish_risk_limit_usage_current_buffer(b, root);
                Ok(())
            },
        )?;
    }
    for reservation in &snapshot.reservations {
        let reservation_id = reservation.reservation_id.as_str();
        insert_indexed(
            &mut values,
            crate::RISK_RESERVATIONS_DATABASE,
            &[reservation_id],
            |b| {
                let actor_id = b.create_string(actor_id);
                let reservation = reservation_state_fb(b, reservation);
                let root = fb::RiskReservationCurrent::create(
                    b,
                    &fb::RiskReservationCurrentArgs {
                        actor_id: Some(actor_id),
                        reservation: Some(reservation),
                    },
                );
                fb::finish_risk_reservation_current_buffer(b, root);
                Ok(())
            },
        )?;
        for value in &reservation.allocations {
            let metric_key = metric_key(value.metric);
            insert_indexed(
                &mut values,
                crate::RISK_ALLOCATIONS_DATABASE,
                &[reservation_id, value.policy_id.as_str(), metric_key],
                |b| {
                    let actor_id = b.create_string(actor_id);
                    let reservation_id = b.create_string(reservation_id);
                    let allocation = allocation(b, value);
                    let root = fb::RiskAllocationCurrent::create(
                        b,
                        &fb::RiskAllocationCurrentArgs {
                            actor_id: Some(actor_id),
                            reservation_id: Some(reservation_id),
                            allocation: Some(allocation),
                        },
                    );
                    fb::finish_risk_allocation_current_buffer(b, root);
                    Ok(())
                },
            )?;
        }
    }
    for circuit in &snapshot.circuits {
        let key = circuit_key(circuit);
        insert_indexed(&mut values, crate::RISK_CIRCUITS_DATABASE, &[&key], |b| {
            let actor_id = b.create_string(actor_id);
            let circuit_key = b.create_string(&key);
            let circuit = circuit_fb(b, circuit)?;
            let root = fb::RiskCircuitCurrent::create(
                b,
                &fb::RiskCircuitCurrentArgs {
                    actor_id: Some(actor_id),
                    circuit_key: Some(circuit_key),
                    circuit: Some(circuit),
                },
            );
            fb::finish_risk_circuit_current_buffer(b, root);
            Ok(())
        })?;
    }
    Ok(values)
}

fn insert_indexed(
    values: &mut BTreeMap<(String, Vec<u8>), Vec<u8>>,
    database: &str,
    parts: &[&str],
    encode: impl FnOnce(&mut FlatBufferBuilder<'_>) -> Result<(), String>,
) -> Result<(), String> {
    let key = crate::risk_indexed_key(parts).map_err(|error| error.to_string())?;
    let mut builder = FlatBufferBuilder::new();
    encode(&mut builder)?;
    values.insert((database.to_owned(), key), builder.finished_data().to_vec());
    Ok(())
}

pub struct FlatbuffersRiskEventWriter {
    pub actor_id: kairos_primitives::runtime::ActorId,
    identity: kairos_primitives::runtime::InstanceIdentity,
    producer_incarnation: u64,
    pub last_payload: Option<Vec<u8>>,
}
pub struct RiskAeronEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    encoder: FlatbuffersRiskEventWriter,
}

impl RiskAeronEventPublisher {
    pub fn connect(
        endpoint: &kairos_transport::AeronEndpoint,
        actor_id: kairos_primitives::runtime::ActorId,
        identity: kairos_primitives::runtime::InstanceIdentity,
    ) -> crate::ContractResult<Self> {
        if endpoint.stream_id() != kairos_transport::stream_ids::RISK_EVENTS {
            return Err(crate::ContractError::Invalid(format!(
                "Risk events require stream id {}, received {}",
                kairos_transport::stream_ids::RISK_EVENTS,
                endpoint.stream_id()
            )));
        }
        Ok(Self {
            publisher: kairos_transport::AeronBytePublisher::connect_endpoint(endpoint)
                .map_err(|error| crate::ContractError::Transport(error.to_string()))?,
            encoder: FlatbuffersRiskEventWriter::new_with_identity(actor_id, identity),
        })
    }
    pub fn publish(&mut self, event: &RiskEvent) -> crate::ContractResult<()> {
        self.encoder
            .publish(event)
            .map_err(crate::ContractError::Invalid)?;
        if let Some(payload) = self.encoder.last_payload.as_deref() {
            self.publisher
                .publish(payload)
                .map_err(|error| crate::ContractError::Transport(error.to_string()))?;
        }
        Ok(())
    }
}

impl FlatbuffersRiskEventWriter {
    pub fn new(actor_id: kairos_primitives::runtime::ActorId) -> Self {
        Self::new_with_identity(actor_id, Default::default())
    }
    pub fn new_with_identity(
        actor_id: kairos_primitives::runtime::ActorId,
        identity: kairos_primitives::runtime::InstanceIdentity,
    ) -> Self {
        Self::new_with_incarnation(actor_id, identity, 1)
    }
    pub fn new_with_incarnation(
        actor_id: kairos_primitives::runtime::ActorId,
        identity: kairos_primitives::runtime::InstanceIdentity,
        producer_incarnation: u64,
    ) -> Self {
        assert!(
            producer_incarnation > 0,
            "producer incarnation must be positive"
        );
        Self {
            actor_id,
            identity,
            producer_incarnation,
            last_payload: None,
        }
    }
    pub fn publish(&mut self, event: &RiskEvent) -> Result<(), String> {
        let mut b = FlatBufferBuilder::new();
        self.last_payload = None;
        match event {
            RiskEvent::PolicyActivated { .. } => return Ok(()),
            RiskEvent::DecisionEvaluated {
                decision,
                event_sequence,
                ..
            } => {
                let decision = decision_fb(&mut b, decision)?;
                let metadata = metadata(
                    &mut b,
                    &self.actor_id,
                    &self.identity,
                    self.producer_incarnation,
                    event_sequence.get(),
                    decision_request_time(event),
                );
                let root = fb::RiskDecisionMade::create(
                    &mut b,
                    &fb::RiskDecisionMadeArgs {
                        metadata: Some(metadata),
                        decision: Some(decision),
                    },
                );
                fb::finish_risk_decision_made_buffer(&mut b, root);
            },
            RiskEvent::ReservationChanged {
                reservation,
                event_sequence,
            } => {
                let value = reservation_fb(&mut b, reservation)?;
                let metadata = metadata(
                    &mut b,
                    &self.actor_id,
                    &self.identity,
                    self.producer_incarnation,
                    event_sequence.get(),
                    reservation.updated_at_unix_nanos.get(),
                );
                match reservation.status {
                    ReservationStatus::Reserved => {
                        let root = fb::ReservationReserved::create(
                            &mut b,
                            &fb::ReservationReservedArgs {
                                metadata: Some(metadata),
                                reservation: Some(value),
                            },
                        );
                        fb::finish_reservation_reserved_buffer(&mut b, root);
                    },
                    ReservationStatus::Consumed => {
                        let root = fb::ReservationConsumed::create(
                            &mut b,
                            &fb::ReservationConsumedArgs {
                                metadata: Some(metadata),
                                reservation: Some(value),
                            },
                        );
                        fb::finish_reservation_consumed_buffer(&mut b, root);
                    },
                    ReservationStatus::Released => {
                        let root = fb::ReservationReleased::create(
                            &mut b,
                            &fb::ReservationReleasedArgs {
                                metadata: Some(metadata),
                                reservation: Some(value),
                                reason: None,
                            },
                        );
                        fb::finish_reservation_released_buffer(&mut b, root);
                    },
                    ReservationStatus::Expired => {
                        let root = fb::ReservationExpired::create(
                            &mut b,
                            &fb::ReservationExpiredArgs {
                                metadata: Some(metadata),
                                reservation: Some(value),
                            },
                        );
                        fb::finish_reservation_expired_buffer(&mut b, root);
                    },
                }
            },
            RiskEvent::CircuitChanged {
                circuit,
                event_sequence,
            } => {
                let value = circuit_fb(&mut b, circuit)?;
                let at = circuit
                    .opened_at_unix_nanos
                    .or(circuit.reset_at_unix_nanos)
                    .unwrap_or_default();
                let metadata = metadata(
                    &mut b,
                    &self.actor_id,
                    &self.identity,
                    self.producer_incarnation,
                    event_sequence.get(),
                    at.get(),
                );
                if circuit.open {
                    let root = fb::CircuitOpened::create(
                        &mut b,
                        &fb::CircuitOpenedArgs {
                            metadata: Some(metadata),
                            circuit: Some(value),
                        },
                    );
                    fb::finish_circuit_opened_buffer(&mut b, root);
                } else {
                    let root = fb::CircuitClosed::create(
                        &mut b,
                        &fb::CircuitClosedArgs {
                            metadata: Some(metadata),
                            circuit: Some(value),
                        },
                    );
                    fb::finish_circuit_closed_buffer(&mut b, root);
                }
            },
        };
        self.last_payload = Some(b.finished_data().to_vec());
        Ok(())
    }
}

fn metadata<'a>(
    b: &mut FlatBufferBuilder<'a>,
    actor: &str,
    identity: &kairos_primitives::runtime::InstanceIdentity,
    producer_incarnation: u64,
    sequence: u64,
    at: u64,
) -> flatbuffers::WIPOffset<common_fb::EventMetadata<'a>> {
    let event_id = b.create_string(&format!("risk:{sequence}"));
    let stream = b.create_string("risk.events");
    let producer = b.create_string(actor);
    let workspace = b.create_string(identity.workspace_id.as_str());
    let launch = identity
        .launch_id()
        .map(|value| b.create_string(value.as_str()));
    let instance = identity
        .instance_id()
        .map(|value| b.create_string(value.as_str()));
    common_fb::EventMetadata::create(
        b,
        &common_fb::EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream),
            sequence,
            producer_id: Some(producer),
            producer_incarnation,
            workspace_id: Some(workspace),
            launch_id: launch,
            instance_id: instance,
            correlation_id: None,
            causation_id: None,
            occurred_at_unix_nanos: at,
            published_at_unix_nanos: now(),
        },
    )
}
fn decimal(value: crate::Amount) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}
fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}
fn decision_request_time(event: &RiskEvent) -> u64 {
    match event {
        RiskEvent::DecisionEvaluated { decision, .. } => decision.evaluated_at_unix_nanos.get(),
        _ => 0,
    }
}

fn metric(value: crate::Metric) -> fb::Metric {
    match value {
        crate::Metric::Notional => fb::Metric::NOTIONAL,
        crate::Metric::Margin => fb::Metric::MARGIN,
        crate::Metric::GrossExposure => fb::Metric::GROSS_EXPOSURE,
        crate::Metric::NetExposure => fb::Metric::NET_EXPOSURE,
        crate::Metric::Turnover => fb::Metric::TURNOVER,
        crate::Metric::OrderRate => fb::Metric::ORDER_RATE,
        crate::Metric::DailyLoss => fb::Metric::DAILY_LOSS,
        crate::Metric::Drawdown => fb::Metric::DRAWDOWN,
        crate::Metric::Leverage => fb::Metric::LEVERAGE,
        crate::Metric::PriceDeviation => fb::Metric::PRICE_DEVIATION,
        crate::Metric::StressLoss => fb::Metric::STRESS_LOSS,
    }
}
fn status(value: ReservationStatus) -> fb::ReservationStatus {
    match value {
        ReservationStatus::Reserved => fb::ReservationStatus::RESERVED,
        ReservationStatus::Consumed => fb::ReservationStatus::CONSUMED,
        ReservationStatus::Released => fb::ReservationStatus::RELEASED,
        ReservationStatus::Expired => fb::ReservationStatus::EXPIRED,
    }
}
fn scope<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::PolicyScope,
) -> flatbuffers::WIPOffset<fb::PolicyScope<'a>> {
    let a = value.account_id.as_deref().map(|x| b.create_string(x));
    let s = value.strategy_id.as_deref().map(|x| b.create_string(x));
    let i = value.instrument_id.as_deref().map(|x| b.create_string(x));
    let e = value.exchange_id.as_deref().map(|x| b.create_string(x));
    fb::PolicyScope::create(
        b,
        &fb::PolicyScopeArgs {
            account_id: a,
            strategy_id: s,
            instrument_id: i,
            exchange_id: e,
        },
    )
}
fn policy<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::RiskPolicy,
) -> flatbuffers::WIPOffset<fb::RiskPolicy<'a>> {
    let id = b.create_string(&value.policy_id);
    let lim = decimal(value.limit);
    let sc = scope(b, &value.scope);
    fb::RiskPolicy::create(
        b,
        &fb::RiskPolicyArgs {
            policy_id: Some(id),
            version: value.version.get(),
            scope: Some(sc),
            metric: metric(value.metric),
            limit: Some(&lim),
            enforcement: fb::EnforcementMode::REJECT,
            valid_from_unix_nanos: value.valid_from_unix_nanos.get(),
            valid_until_unix_nanos: value.valid_until_unix_nanos.map(|item| item.get()),
            window_nanos: value.window_nanos.map(|item| item.get()),
        },
    )
}
fn allocation<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::Allocation,
) -> flatbuffers::WIPOffset<fb::Allocation<'a>> {
    let id = b.create_string(&value.policy_id);
    let amount = decimal(value.amount);
    fb::Allocation::create(
        b,
        &fb::AllocationArgs {
            policy_id: Some(id),
            metric: metric(value.metric),
            amount: Some(&amount),
        },
    )
}

fn reservation_state_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::Reservation,
) -> flatbuffers::WIPOffset<fb::RiskReservationState<'a>> {
    let reservation_id = b.create_string(value.reservation_id.as_str());
    let request_id = b.create_string(value.request_id.as_str());
    let account_id = value
        .account_id
        .as_deref()
        .map(|value| b.create_string(value));
    let strategy_id = value
        .strategy_id
        .as_deref()
        .map(|value| b.create_string(value));
    let idempotency_key = b.create_string(value.idempotency_key.as_str());
    fb::RiskReservationState::create(
        b,
        &fb::RiskReservationStateArgs {
            reservation_id: Some(reservation_id),
            request_id: Some(request_id),
            account_id,
            strategy_id,
            idempotency_key: Some(idempotency_key),
            status: status(value.status),
            created_at_unix_nanos: value.created_at_unix_nanos.get(),
            updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
            expires_at_unix_nanos: value.expires_at_unix_nanos.get(),
            policy_version: value.policy_version.get(),
        },
    )
}
fn reservation_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::Reservation,
) -> Result<flatbuffers::WIPOffset<fb::Reservation<'a>>, String> {
    let id = b.create_string(&value.reservation_id);
    let req = b.create_string(&value.request_id);
    let account = value.account_id.as_deref().map(|x| b.create_string(x));
    let strategy = value.strategy_id.as_deref().map(|x| b.create_string(x));
    let instrument = b.create_string("");
    let idem = b.create_string(&value.idempotency_key);
    let usages = b.create_vector::<flatbuffers::WIPOffset<fb::RiskUsage>>(&[]);
    let alloc = value
        .allocations
        .iter()
        .map(|x| allocation(b, x))
        .collect::<Vec<_>>();
    let alloc = b.create_vector(&alloc);
    Ok(fb::Reservation::create(
        b,
        &fb::ReservationArgs {
            reservation_id: Some(id),
            request_id: Some(req),
            account_id: account,
            strategy_id: strategy,
            instrument_id: Some(instrument),
            idempotency_key: Some(idem),
            requested_usages: Some(usages),
            allocations: Some(alloc),
            status: status(value.status),
            created_at_unix_nanos: value.created_at_unix_nanos.get(),
            updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
            expires_at_unix_nanos: value.expires_at_unix_nanos.get(),
            policy_version: value.policy_version.get(),
        },
    ))
}
fn circuit_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::CircuitState,
) -> Result<flatbuffers::WIPOffset<fb::CircuitState<'a>>, String> {
    let key = circuit_key(value);
    let id = b.create_string(&key);
    let account = value
        .scope
        .account_id
        .as_deref()
        .map(|x| b.create_string(x));
    let strategy = value
        .scope
        .strategy_id
        .as_deref()
        .map(|x| b.create_string(x));
    let exchange = value
        .scope
        .exchange_id
        .as_deref()
        .map(|x| b.create_string(x));
    let sc = fb::CircuitScope::create(
        b,
        &fb::CircuitScopeArgs {
            account_id: account,
            strategy_id: strategy,
            exchange_id: exchange,
        },
    );
    let reason = b.create_string(&value.reason);
    Ok(fb::CircuitState::create(
        b,
        &fb::CircuitStateArgs {
            circuit_id: Some(id),
            scope: Some(sc),
            status: if value.open {
                fb::CircuitStatus::OPEN
            } else {
                fb::CircuitStatus::CLOSED
            },
            opened_at_unix_nanos: value.opened_at_unix_nanos.map(|item| item.get()),
            reset_at_unix_nanos: value.reset_at_unix_nanos.map(|item| item.get()),
            reason: Some(reason),
        },
    ))
}

fn circuit_key(value: &crate::control::CircuitState) -> String {
    format!(
        "account={};strategy={};exchange={}",
        value.scope.account_id.as_deref().unwrap_or("*"),
        value.scope.strategy_id.as_deref().unwrap_or("*"),
        value.scope.exchange_id.as_deref().unwrap_or("*")
    )
}

fn metric_key(value: crate::Metric) -> &'static str {
    match value {
        crate::Metric::Notional => "NOTIONAL",
        crate::Metric::Margin => "MARGIN",
        crate::Metric::GrossExposure => "GROSS_EXPOSURE",
        crate::Metric::NetExposure => "NET_EXPOSURE",
        crate::Metric::Turnover => "TURNOVER",
        crate::Metric::OrderRate => "ORDER_RATE",
        crate::Metric::DailyLoss => "DAILY_LOSS",
        crate::Metric::Drawdown => "DRAWDOWN",
        crate::Metric::Leverage => "LEVERAGE",
        crate::Metric::PriceDeviation => "PRICE_DEVIATION",
        crate::Metric::StressLoss => "STRESS_LOSS",
    }
}
fn context_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::RiskContext,
) -> Result<flatbuffers::WIPOffset<fb::RiskContext<'a>>, String> {
    let evidence = b.create_vector::<flatbuffers::WIPOffset<common_fb::EvidenceRef>>(&[]);
    let current_exposure = decimal(value.current_exposure);
    let current_margin = decimal(value.current_margin);
    let available_margin = decimal(value.available_margin);
    let current_pnl = decimal(value.current_pnl);
    let current_drawdown = decimal(value.current_drawdown);
    let stress_loss = decimal(value.stress_loss);
    Ok(fb::RiskContext::create(
        b,
        &fb::RiskContextArgs {
            evidence: Some(evidence),
            current_exposure: Some(&current_exposure),
            current_margin: Some(&current_margin),
            available_margin: Some(&available_margin),
            current_pnl: Some(&current_pnl),
            current_drawdown: Some(&current_drawdown),
            market_freshness: if value.market_is_fresh {
                fb::DependencyFreshness::FRESH
            } else {
                fb::DependencyFreshness::STALE
            },
            leverage_bps: value.leverage_bps.get(),
            price_deviation_bps: value.price_deviation_bps.get(),
            stress_loss: Some(&stress_loss),
        },
    ))
}
fn decision_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &RiskDecision,
) -> Result<flatbuffers::WIPOffset<fb::RiskDecision<'a>>, String> {
    let id = b.create_string(&value.decision_id);
    let req = b.create_string(&value.request_id);
    let account = b.create_string(&value.account_id);
    let strategy = b.create_string(&value.strategy_id);
    let instrument = b.create_string(value.instrument_id.as_str());
    let reasons = b.create_vector(&[] as &[flatbuffers::WIPOffset<fb::DecisionReason>]);
    let allocs = value
        .allocations
        .iter()
        .map(|x| allocation(b, x))
        .collect::<Vec<_>>();
    let allocs = b.create_vector(&allocs);
    let reservation = value
        .reservation
        .as_ref()
        .map(|x| reservation_fb(b, x))
        .transpose()?;
    let zero = crate::Amount::default();
    let fallback = crate::RiskContext {
        account_snapshot_watermark: 0.into(),
        market_freshness_watermark: 0.into(),
        portfolio_version: 0.into(),
        current_exposure: zero,
        current_margin: zero,
        available_margin: zero,
        current_pnl: zero,
        current_drawdown: zero,
        market_is_fresh: true,
        leverage_bps: 0.into(),
        price_deviation_bps: 0.into(),
        stress_loss: zero,
    };
    let context = context_fb(b, value.context.as_ref().unwrap_or(&fallback))?;
    let funding_requirement = value
        .funding_requirement
        .as_ref()
        .map(|value| {
            let required_margin = decimal(value.required_margin);
            let available_margin = decimal(value.available_margin);
            let shortfall = decimal(value.shortfall);
            let margin_rule_id = b.create_string(&value.margin_rule_id);
            let account_segment = b.create_string(value.account_segment.as_str());
            let collateral_asset = b.create_string(value.collateral_asset.as_str());
            Ok::<_, String>(fb::FundingRequirement::create(
                b,
                &fb::FundingRequirementArgs {
                    required_margin: Some(&required_margin),
                    available_margin: Some(&available_margin),
                    shortfall: Some(&shortfall),
                    margin_rule_id: Some(margin_rule_id),
                    account_segment: Some(account_segment),
                    collateral_asset: Some(collateral_asset),
                },
            ))
        })
        .transpose()?;
    Ok(fb::RiskDecision::create(
        b,
        &fb::RiskDecisionArgs {
            decision_id: Some(id),
            request_id: Some(req),
            account_id: Some(account),
            strategy_id: Some(strategy),
            instrument_id: Some(instrument),
            outcome: if value.allowed {
                if value.degraded {
                    fb::DecisionOutcome::DEGRADED_ALLOWED
                } else {
                    fb::DecisionOutcome::ALLOWED
                }
            } else {
                fb::DecisionOutcome::REJECTED
            },
            reasons: Some(reasons),
            allocations: Some(allocs),
            reservation,
            policy_version: value.policy_version.get(),
            context: Some(context),
            funding_requirement,
            evaluated_at_unix_nanos: value.evaluated_at_unix_nanos.get(),
        },
    ))
}
