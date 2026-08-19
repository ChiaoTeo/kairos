//! FlatBuffers v2 encoders for the Risk boundary.
//!
//! Current state is `RXV2`; durable facts use one typed root per fact.

use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::common::v_2::{self as common_fb, Decimal64};
use kairos_protocol::generated::kairos::risk::v_2 as fb;

use crate::control::{ReservationStatus, RiskCurrentView, RiskDecision, RiskEvent};

pub struct FlatbuffersRiskSnapshotWriter {
    pub actor_id: String,
    pub last_payload: Option<Vec<u8>>,
}

pub struct MmapRiskSnapshotPublisher {
    publisher: crate::RiskViewPublisher,
    encoder: FlatbuffersRiskSnapshotWriter,
    producer_incarnation: u64,
}

impl MmapRiskSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> crate::ContractResult<Self> {
        Ok(Self {
            publisher: crate::RiskViewPublisher::create(
                path,
                crate::view::RiskViewKey::latest(actor_id.into()),
                slot_size,
            )?,
            encoder: FlatbuffersRiskSnapshotWriter::new("risk"),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }
    pub fn publish(&mut self, snapshot: &RiskCurrentView) -> crate::ContractResult<()> {
        self.encoder
            .publish(snapshot)
            .map_err(crate::ContractError::Invalid)?;
        self.publisher.publish(
            kairos_transport::SnapshotEnvelopeMetadata {
                resource_epoch: 1,
                producer_incarnation: self.producer_incarnation,
                generation: snapshot.generation.get(),
                applied_event_sequence: snapshot.event_sequence.get(),
                published_at_unix_nanos: now_unix_nanos(),
            },
            self.encoder.last_payload.as_deref().unwrap_or_default(),
        )
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            last_payload: None,
        }
    }
    pub fn publish(&mut self, snapshot: &RiskCurrentView) -> Result<(), String> {
        let mut b = FlatBufferBuilder::new();
        let limits = snapshot
            .limits
            .iter()
            .map(|value| limit(&mut b, value))
            .collect::<Result<Vec<_>, _>>()?;
        let reservations = snapshot
            .reservations
            .iter()
            .map(|value| reservation_fb(&mut b, value))
            .collect::<Result<Vec<_>, _>>()?;
        let circuits = snapshot
            .circuits
            .iter()
            .map(|value| circuit_fb(&mut b, value))
            .collect::<Result<Vec<_>, _>>()?;
        let limits = b.create_vector(&limits);
        let reservations = b.create_vector(&reservations);
        let circuits = b.create_vector(&circuits);
        let state = fb::RiskLatestState::create(
            &mut b,
            &fb::RiskLatestStateArgs {
                policy_version: snapshot.policy_version.get(),
                limits: Some(limits),
                active_reservations: Some(reservations),
                circuits: Some(circuits),
            },
        );
        let snapshot_id = b.create_string(&format!("risk-{}", snapshot.generation));
        let view_key = b.create_string("risk.latest");
        let owner = b.create_string(&self.actor_id);
        let workspace = b.create_string(&self.actor_id);
        let metadata = common_fb::ViewMetadata::create(
            &mut b,
            &common_fb::ViewMetadataArgs {
                snapshot_id: Some(snapshot_id),
                resource_id: Some(view_key),
                resource_epoch: 1,
                view_key: Some(view_key),
                owner_id: Some(owner),
                workspace_id: Some(workspace),
                launch_id: None,
                instance_id: None,
                generation: snapshot.generation.get(),
                as_of_unix_nanos: as_of(snapshot),
                published_at_unix_nanos: now(),
                completeness: common_fb::ViewCompleteness::COMPLETE,
                applied_revision: Some(snapshot.event_sequence.get()),
            },
        );
        let root = fb::RiskLatestView::create(
            &mut b,
            &fb::RiskLatestViewArgs {
                metadata: Some(metadata),
                state: Some(state),
            },
        );
        fb::finish_risk_latest_view_buffer(&mut b, root);
        self.last_payload = Some(b.finished_data().to_vec());
        Ok(())
    }
}

pub struct FlatbuffersRiskEventWriter {
    pub actor_id: String,
    identity: kairos_primitives::runtime::InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
}
pub struct RiskAeronEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    encoder: FlatbuffersRiskEventWriter,
}

impl RiskAeronEventPublisher {
    pub fn connect(
        endpoint: &kairos_transport::AeronEndpoint,
        actor_id: impl Into<String>,
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
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(actor_id, Default::default())
    }
    pub fn new_with_identity(
        actor_id: impl Into<String>,
        identity: kairos_primitives::runtime::InstanceIdentity,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            identity,
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
fn as_of(value: &RiskCurrentView) -> u64 {
    value
        .reservations
        .iter()
        .map(|x| x.updated_at_unix_nanos)
        .max()
        .unwrap_or_default()
        .get()
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
fn limit<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::LimitView,
) -> Result<flatbuffers::WIPOffset<fb::LimitUsage<'a>>, String> {
    let p = policy(b, &value.policy);
    let used = decimal(value.used);
    let reserved = decimal(value.reserved);
    let available = decimal(value.available);
    Ok(fb::LimitUsage::create(
        b,
        &fb::LimitUsageArgs {
            policy: Some(p),
            used: Some(&used),
            reserved: Some(&reserved),
            available: Some(&available),
        },
    ))
}
fn circuit_fb<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::control::CircuitState,
) -> Result<flatbuffers::WIPOffset<fb::CircuitState<'a>>, String> {
    let id = b.create_string("risk-circuit");
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
    let instrument = value
        .instrument_id
        .as_ref()
        .map(|item| b.create_string(item.as_str()));
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
            Ok::<_, String>(fb::FundingRequirement::create(
                b,
                &fb::FundingRequirementArgs {
                    required_margin: Some(&required_margin),
                    available_margin: Some(&available_margin),
                    shortfall: Some(&shortfall),
                    margin_rule_id: Some(margin_rule_id),
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
            instrument_id: instrument,
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
