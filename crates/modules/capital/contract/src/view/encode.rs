use flatbuffers::{FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::ProtocolContext;
use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;

use super::{CapitalViewKey, CapitalViewPublisher};
use crate::{
    CapitalAlert, CapitalAlertKind, CapitalAlertSeverity, CapitalAvailability, CapitalCurrentView,
    CapitalDemand, CapitalDemandLifecycleStatus, CapitalEarnHolding, CapitalFacts,
    CapitalFundingHorizon, CapitalOperation, CapitalOperationKind, CapitalOperationStatus,
    CapitalPlan, CapitalPlanStatus, CapitalPolicy, CapitalReadiness, CapitalRecoveryAction,
    CapitalReservation, CapitalReservationStatus, CapitalRoute, CapitalRouteKind,
    CapitalSettlementClass, ContractError, ContractResult, FundingLocation, FundingObjective,
    FundingObjectiveLifecycleStatus, FundingPriority,
};

pub struct FlatbuffersCapitalViewWriter {
    owner_id: String,
    identity: InstanceIdentity,
    key: CapitalViewKey,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersCapitalViewWriter {
    pub fn new(
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        key: CapitalViewKey,
    ) -> Self {
        Self {
            owner_id: owner_id.into(),
            identity,
            key,
            last_payload: None,
        }
    }

    pub fn publish(&mut self, view: &CapitalCurrentView) -> Result<(), String> {
        if self.key.capital_group_id != view.capital_group_id.as_str() {
            return Err("Capital view key does not match capital_group_id".into());
        }
        let mut builder = FlatBufferBuilder::new();
        let availability = view
            .availability
            .iter()
            .map(|value| availability(&mut builder, value))
            .collect::<Vec<_>>();
        let plans = view
            .plans
            .iter()
            .map(|value| plan(&mut builder, value))
            .collect::<Vec<_>>();
        let operations = view
            .operations
            .iter()
            .map(|value| operation(&mut builder, value))
            .collect::<Vec<_>>();
        let alerts = view
            .alerts
            .iter()
            .map(|value| alert(&mut builder, value))
            .collect::<Vec<_>>();
        let objectives = view
            .objectives
            .iter()
            .map(|value| objective(&mut builder, value))
            .collect::<Vec<_>>();
        let demands = view
            .demands
            .iter()
            .map(|value| demand(&mut builder, value))
            .collect::<Vec<_>>();
        let policies = view
            .policies
            .iter()
            .map(|value| policy(&mut builder, value))
            .collect::<Vec<_>>();
        let facts = view
            .facts
            .iter()
            .map(|value| capital_facts(&mut builder, value))
            .collect::<Vec<_>>();
        let routes = view
            .routes
            .iter()
            .map(|value| route(&mut builder, value))
            .collect::<Vec<_>>();
        let reservations = view
            .reservations
            .iter()
            .map(|value| reservation(&mut builder, value))
            .collect::<Vec<_>>();
        let objectives = builder.create_vector(&objectives);
        let demands = builder.create_vector(&demands);
        let policies = builder.create_vector(&policies);
        let facts = builder.create_vector(&facts);
        let availability = builder.create_vector(&availability);
        let routes = builder.create_vector(&routes);
        let plans = builder.create_vector(&plans);
        let reservations = builder.create_vector(&reservations);
        let operations = builder.create_vector(&operations);
        let alerts = builder.create_vector(&alerts);
        let group_id = builder.create_string(view.capital_group_id.as_str());
        let strategy_id = builder.create_string(view.strategy_id.as_str());
        let environment = builder.create_string(&view.environment);
        let state = fb::CapitalCurrentState::create(
            &mut builder,
            &fb::CapitalCurrentStateArgs {
                capital_group_id: Some(group_id),
                strategy_id: Some(strategy_id),
                environment: Some(environment),
                membership_version: view.membership_version.get(),
                event_sequence: view.event_sequence.get(),
                journal_sequence: view.journal_sequence.get(),
                objectives: Some(objectives),
                demands: Some(demands),
                policies: Some(policies),
                facts: Some(facts),
                availability: Some(availability),
                routes: Some(routes),
                plans: Some(plans),
                reservations: Some(reservations),
                operations: Some(operations),
                alerts: Some(alerts),
            },
        );
        let canonical_key = self.key.canonical_key();
        let context = ProtocolContext::view(
            "capital",
            &self.owner_id,
            self.identity.clone(),
            view.event_sequence.get(),
            &canonical_key,
        )?;
        let snapshot_id = format!("{canonical_key}:{}", view.event_sequence);
        let metadata = kairos_protocol::metadata::view_metadata(
            &mut builder,
            &context,
            &snapshot_id,
            &canonical_key,
            as_of(view),
            Some(view.event_sequence.get()),
        );
        let root = fb::CapitalCurrentView::create(
            &mut builder,
            &fb::CapitalCurrentViewArgs {
                metadata: Some(metadata),
                state: Some(state),
            },
        );
        fb::finish_capital_current_view_buffer(&mut builder, root);
        self.last_payload = Some(builder.finished_data().to_vec());
        Ok(())
    }
}

pub struct MmapCapitalViewPublisher {
    publisher: CapitalViewPublisher,
    encoder: FlatbuffersCapitalViewWriter,
    producer_incarnation: u64,
}

impl MmapCapitalViewPublisher {
    pub fn create(
        root: impl AsRef<std::path::Path>,
        slot_capacity: usize,
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        capital_group_id: impl Into<String>,
    ) -> ContractResult<Self> {
        let key = CapitalViewKey::current(capital_group_id);
        Ok(Self {
            publisher: CapitalViewPublisher::create(root, key.clone(), slot_capacity)?,
            encoder: FlatbuffersCapitalViewWriter::new(owner_id, identity, key),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }

    pub fn publish(&mut self, view: &CapitalCurrentView) -> ContractResult<()> {
        self.encoder.publish(view).map_err(ContractError::Invalid)?;
        self.publisher.publish(
            kairos_transport::SnapshotEnvelopeMetadata {
                resource_epoch: 1,
                producer_incarnation: self.producer_incarnation,
                generation: view.event_sequence.get(),
                applied_event_sequence: view.event_sequence.get(),
                published_at_unix_nanos: now_unix_nanos(),
            },
            self.encoder.last_payload.as_deref().unwrap_or_default(),
        )
    }
}

pub(crate) fn location<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &FundingLocation,
) -> WIPOffset<fb::FundingLocation<'a>> {
    let broker = builder.create_string(value.broker.as_str());
    let account_id = builder.create_string(value.account_id.as_str());
    let segment = builder.create_string(value.segment.as_str());
    let asset = builder.create_string(value.asset.as_str());
    fb::FundingLocation::create(
        builder,
        &fb::FundingLocationArgs {
            broker: Some(broker),
            account_id: Some(account_id),
            segment: Some(segment),
            asset: Some(asset),
        },
    )
}

fn decimal(value: kairos_primitives::decimal::Quantity) -> Decimal64 {
    Decimal64::new(value.mantissa(), value.scale())
}

fn strings<'a, I, T>(
    builder: &mut FlatBufferBuilder<'a>,
    values: I,
) -> WIPOffset<flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<&'a str>>>
where
    I: IntoIterator<Item = T>,
    T: AsRef<str>,
{
    let values = values
        .into_iter()
        .map(|value| builder.create_string(value.as_ref()))
        .collect::<Vec<_>>();
    builder.create_vector(&values)
}

fn priority(value: FundingPriority) -> fb::FundingPriority {
    match value {
        FundingPriority::Low => fb::FundingPriority::LOW,
        FundingPriority::Normal => fb::FundingPriority::NORMAL,
        FundingPriority::High => fb::FundingPriority::HIGH,
        FundingPriority::Critical => fb::FundingPriority::CRITICAL,
    }
}

pub(crate) fn objective<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &FundingObjective,
) -> WIPOffset<fb::FundingObjective<'a>> {
    let objective_id = builder.create_string(value.objective_id.as_str());
    let strategy_id = builder.create_string(value.strategy_id.as_str());
    let destination = location(builder, &value.destination);
    let desired_available = decimal(value.desired_available);
    let strategy_decision_id = builder.create_string(value.strategy_decision_id.as_str());
    fb::FundingObjective::create(
        builder,
        &fb::FundingObjectiveArgs {
            objective_id: Some(objective_id),
            version: value.version.get(),
            strategy_id: Some(strategy_id),
            destination: Some(destination),
            desired_available: Some(&desired_available),
            required_by_unix_nanos: value.required_by.get(),
            expires_at_unix_nanos: value.expires_at.get(),
            priority: priority(value.priority),
            confidence_bps: value.confidence_bps.get() as u16,
            strategy_decision_id: Some(strategy_decision_id),
            status: match value.status {
                FundingObjectiveLifecycleStatus::Active => fb::FundingObjectiveStatus::ACTIVE,
                FundingObjectiveLifecycleStatus::Cancelled => fb::FundingObjectiveStatus::CANCELLED,
                FundingObjectiveLifecycleStatus::Expired => fb::FundingObjectiveStatus::EXPIRED,
            },
            updated_at_unix_nanos: value.updated_at.get(),
        },
    )
}

pub(crate) fn demand<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalDemand,
) -> WIPOffset<fb::CapitalDemand<'a>> {
    let demand_id = builder.create_string(value.demand_id.as_str());
    let idempotency_key = builder.create_string(value.idempotency_key.as_str());
    let strategy_id = builder.create_string(value.strategy_id.as_str());
    let destination = location(builder, &value.destination);
    let observed_shortfall = decimal(value.observed_shortfall);
    let launch_id = builder.create_string(&value.launch_id);
    let instance_id = builder.create_string(&value.instance_id);
    let causal_references = strings(builder, value.causal_references.iter());
    fb::CapitalDemand::create(
        builder,
        &fb::CapitalDemandArgs {
            demand_id: Some(demand_id),
            idempotency_key: Some(idempotency_key),
            strategy_id: Some(strategy_id),
            destination: Some(destination),
            observed_shortfall: Some(&observed_shortfall),
            observed_at_unix_nanos: value.observed_at.get(),
            required_by_unix_nanos: value.required_by.get(),
            expires_at_unix_nanos: value.expires_at.get(),
            priority: priority(value.priority),
            confidence_bps: value.confidence_bps.get() as u16,
            account_watermark: value.account_watermark.get(),
            risk_watermark: value.risk_watermark.get(),
            launch_id: Some(launch_id),
            instance_id: Some(instance_id),
            causal_references: Some(causal_references),
            status: match value.status {
                CapitalDemandLifecycleStatus::Active => fb::CapitalDemandStatus::ACTIVE,
                CapitalDemandLifecycleStatus::Expired => fb::CapitalDemandStatus::EXPIRED,
            },
            updated_at_unix_nanos: value.updated_at.get(),
        },
    )
}

pub(crate) fn policy<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalPolicy,
) -> WIPOffset<fb::CapitalPolicy<'a>> {
    let destination = location(builder, &value.destination);
    let minimum = decimal(value.minimum);
    let default_target = decimal(value.default_target);
    let maximum = decimal(value.maximum);
    let stress_buffer = decimal(value.stress_buffer);
    let minimum_movement = decimal(value.minimum_movement);
    let hysteresis = decimal(value.hysteresis);
    fb::CapitalPolicy::create(
        builder,
        &fb::CapitalPolicyArgs {
            destination: Some(destination),
            version: value.version.get(),
            minimum: Some(&minimum),
            default_target: Some(&default_target),
            maximum: Some(&maximum),
            stress_buffer: Some(&stress_buffer),
            minimum_movement: Some(&minimum_movement),
            hysteresis: Some(&hysteresis),
            deficit_dwell_nanos: value.deficit_dwell_nanos.into(),
            cooldown_nanos: value.cooldown_nanos.into(),
            max_fact_age_nanos: value.max_fact_age_nanos.into(),
        },
    )
}

pub(crate) fn capital_facts<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalFacts,
) -> WIPOffset<fb::CapitalFacts<'a>> {
    let destination = location(builder, &value.destination);
    let observed_available = decimal(value.observed_available);
    let risk_capacity = decimal(value.risk_capacity);
    let earn_holdings = value
        .earn_holdings
        .iter()
        .map(|holding| earn_holding(builder, holding))
        .collect::<Vec<_>>();
    let earn_holdings = builder.create_vector(&earn_holdings);
    fb::CapitalFacts::create(
        builder,
        &fb::CapitalFactsArgs {
            destination: Some(destination),
            observed_available: Some(&observed_available),
            account_watermark: value.account_watermark.get(),
            account_observed_at_unix_nanos: value.account_observed_at.get(),
            account_complete: value.account_complete,
            risk_capacity: Some(&risk_capacity),
            risk_policy_version: value.risk_policy_version.get(),
            risk_watermark: value.risk_watermark.get(),
            earn_holdings: Some(earn_holdings),
        },
    )
}

fn earn_holding<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalEarnHolding,
) -> WIPOffset<fb::CapitalEarnHolding<'a>> {
    let product_id = builder.create_string(&value.product_id);
    let principal = decimal(value.principal);
    let redeemable_amount = decimal(value.redeemable_amount);
    fb::CapitalEarnHolding::create(
        builder,
        &fb::CapitalEarnHoldingArgs {
            product_id: Some(product_id),
            principal: Some(&principal),
            redeemable_amount: Some(&redeemable_amount),
            immediately_redeemable: value.immediately_redeemable,
            active: value.active,
        },
    )
}

pub(crate) fn route<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalRoute,
) -> WIPOffset<fb::CapitalRoute<'a>> {
    let route_id = builder.create_string(value.route_id.as_str());
    let source = location(builder, &value.source);
    let destination = location(builder, &value.destination);
    let per_operation_limit = decimal(value.per_operation_limit);
    let daily_limit = decimal(value.daily_limit);
    let authority = builder.create_string(&value.required_source_authority);
    let earn_product_id = value
        .earn_product_id
        .as_deref()
        .map(|value| builder.create_string(value));
    fb::CapitalRoute::create(
        builder,
        &fb::CapitalRouteArgs {
            route_id: Some(route_id),
            version: value.version.get(),
            source: Some(source),
            destination: Some(destination),
            kind: match value.kind {
                CapitalRouteKind::InternalTransfer => fb::CapitalRouteKind::INTERNAL_TRANSFER,
                CapitalRouteKind::AccountTransfer => fb::CapitalRouteKind::ACCOUNT_TRANSFER,
                CapitalRouteKind::EarnRedemptionThenTransfer => {
                    fb::CapitalRouteKind::EARN_REDEMPTION_THEN_TRANSFER
                },
                CapitalRouteKind::EarnSubscription => fb::CapitalRouteKind::EARN_SUBSCRIPTION,
            },
            per_operation_limit: Some(&per_operation_limit),
            daily_limit: Some(&daily_limit),
            required_source_authority: Some(authority),
            settlement_class: match value.settlement_class {
                CapitalSettlementClass::ImmediateBookTransfer => {
                    fb::CapitalSettlementClass::IMMEDIATE_BOOK_TRANSFER
                },
                CapitalSettlementClass::ParticipantHistoryThenAccountObservation => {
                    fb::CapitalSettlementClass::PARTICIPANT_HISTORY_THEN_ACCOUNT_OBSERVATION
                },
            },
            enabled: value.enabled,
            earn_product_id,
            demand_guard_nanos: value.demand_guard_nanos.into(),
            allow_unknown_redemption_quota: value.allow_unknown_redemption_quota,
        },
    )
}

pub(crate) fn reservation<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalReservation,
) -> WIPOffset<fb::CapitalReservation<'a>> {
    let reservation_id = builder.create_string(value.reservation_id.as_str());
    let plan_id = builder.create_string(value.plan_id.as_str());
    let source = location(builder, &value.source);
    let amount = decimal(value.amount);
    fb::CapitalReservation::create(
        builder,
        &fb::CapitalReservationArgs {
            reservation_id: Some(reservation_id),
            plan_id: Some(plan_id),
            source: Some(source),
            amount: Some(&amount),
            source_account_watermark: value.source_account_watermark.get(),
            status: match value.status {
                CapitalReservationStatus::Active => fb::CapitalReservationStatus::ACTIVE,
                CapitalReservationStatus::Consumed => fb::CapitalReservationStatus::CONSUMED,
                CapitalReservationStatus::Released => fb::CapitalReservationStatus::RELEASED,
                CapitalReservationStatus::Expired => fb::CapitalReservationStatus::EXPIRED,
            },
            created_at_unix_nanos: value.created_at.get(),
            expires_at_unix_nanos: value.expires_at.get(),
        },
    )
}

pub(crate) fn availability<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalAvailability,
) -> WIPOffset<fb::CapitalAvailability<'a>> {
    let destination = location(builder, &value.destination);
    let objective_ids = strings(builder, value.active_objective_ids.iter());
    let demand_ids = strings(builder, value.active_demand_ids.iter());
    let funding_horizons = value
        .funding_horizons
        .iter()
        .map(|horizon| funding_horizon(builder, horizon))
        .collect::<Vec<_>>();
    let funding_horizons = builder.create_vector(&funding_horizons);
    let desired_target = decimal(value.desired_target);
    let effective_target = decimal(value.effective_target);
    let observed_available = decimal(value.observed_available);
    let deficit = decimal(value.deficit);
    let reason = value
        .reason
        .as_deref()
        .map(|reason| builder.create_string(reason));
    fb::CapitalAvailability::create(
        builder,
        &fb::CapitalAvailabilityArgs {
            destination: Some(destination),
            readiness: match value.readiness {
                CapitalReadiness::WaitingForFacts => fb::CapitalReadiness::WAITING_FOR_FACTS,
                CapitalReadiness::WaitingForAccounts => fb::CapitalReadiness::WAITING_FOR_ACCOUNTS,
                CapitalReadiness::Degraded => fb::CapitalReadiness::DEGRADED,
                CapitalReadiness::Ready => fb::CapitalReadiness::READY,
            },
            policy_version: value.policy_version.get(),
            active_objective_ids: Some(objective_ids),
            active_demand_ids: Some(demand_ids),
            funding_horizons: Some(funding_horizons),
            desired_target: Some(&desired_target),
            effective_target: Some(&effective_target),
            observed_available: Some(&observed_available),
            deficit: Some(&deficit),
            deficit_observed_since_unix_nanos: value
                .deficit_observed_since
                .map(|value| value.get()),
            cooldown_until_unix_nanos: value.cooldown_until.map(|value| value.get()),
            account_watermark: value.account_watermark.get(),
            risk_policy_version: value.risk_policy_version.get(),
            risk_watermark: value.risk_watermark.get(),
            evaluated_at_unix_nanos: value.evaluated_at.get(),
            reason,
        },
    )
}

fn funding_horizon<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalFundingHorizon,
) -> WIPOffset<fb::CapitalFundingHorizon<'a>> {
    let objective_ids = strings(builder, value.objective_ids.iter());
    let demand_ids = strings(builder, value.demand_ids.iter());
    let desired_available = decimal(value.desired_available);
    fb::CapitalFundingHorizon::create(
        builder,
        &fb::CapitalFundingHorizonArgs {
            required_by_unix_nanos: value.required_by.get(),
            objective_ids: Some(objective_ids),
            demand_ids: Some(demand_ids),
            desired_available: Some(&desired_available),
        },
    )
}

pub(crate) fn plan<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalPlan,
) -> WIPOffset<fb::CapitalPlan<'a>> {
    let plan_id = builder.create_string(value.plan_id.as_str());
    let decision_id = builder.create_string(&value.rebalance_decision_id);
    let route_id = builder.create_string(value.route_id.as_str());
    let source = location(builder, &value.source);
    let destination = location(builder, &value.destination);
    let amount = decimal(value.amount);
    let objective_ids = strings(builder, value.objective_ids.iter());
    let demand_ids = strings(builder, value.demand_ids.iter());
    let reservation_id = builder.create_string(value.reservation_id.as_str());
    let idempotency_key = builder.create_string(value.idempotency_key.as_str());
    let selected_earn_product_id = value
        .selected_earn_product_id
        .as_deref()
        .map(|value| builder.create_string(value));
    let source_observed_available = decimal(value.source_observed_available);
    let destination_observed_available = decimal(value.destination_observed_available);
    let redemption_observed_available = value.redemption_observed_available.map(decimal);
    let earn_principal_before = decimal(value.earn_principal_before);
    let recovery_reason = value
        .recovery_reason
        .as_deref()
        .map(|value| builder.create_string(value));
    fb::CapitalPlan::create(
        builder,
        &fb::CapitalPlanArgs {
            plan_id: Some(plan_id),
            rebalance_decision_id: Some(decision_id),
            route_id: Some(route_id),
            route_version: value.route_version.get(),
            route_kind: match value.route_kind {
                CapitalRouteKind::InternalTransfer => fb::CapitalRouteKind::INTERNAL_TRANSFER,
                CapitalRouteKind::AccountTransfer => fb::CapitalRouteKind::ACCOUNT_TRANSFER,
                CapitalRouteKind::EarnRedemptionThenTransfer => {
                    fb::CapitalRouteKind::EARN_REDEMPTION_THEN_TRANSFER
                },
                CapitalRouteKind::EarnSubscription => fb::CapitalRouteKind::EARN_SUBSCRIPTION,
            },
            source: Some(source),
            destination: Some(destination),
            amount: Some(&amount),
            objective_ids: Some(objective_ids),
            demand_ids: Some(demand_ids),
            reservation_id: Some(reservation_id),
            idempotency_key: Some(idempotency_key),
            selected_earn_product_id,
            source_account_watermark: value.source_account_watermark.get(),
            destination_account_watermark: value.destination_account_watermark.get(),
            source_observed_available: Some(&source_observed_available),
            destination_observed_available: Some(&destination_observed_available),
            redemption_account_watermark: value
                .redemption_account_watermark
                .map(|value| value.get()),
            redemption_observed_available: redemption_observed_available.as_ref(),
            earn_principal_before: Some(&earn_principal_before),
            status: match value.status {
                CapitalPlanStatus::Authorized => fb::CapitalPlanStatus::AUTHORIZED,
                CapitalPlanStatus::Subscribing => fb::CapitalPlanStatus::SUBSCRIBING,
                CapitalPlanStatus::AwaitingSubscription => {
                    fb::CapitalPlanStatus::AWAITING_SUBSCRIPTION
                },
                CapitalPlanStatus::Redeeming => fb::CapitalPlanStatus::REDEEMING,
                CapitalPlanStatus::AwaitingRedemption => fb::CapitalPlanStatus::AWAITING_REDEMPTION,
                CapitalPlanStatus::Transferring => fb::CapitalPlanStatus::TRANSFERRING,
                CapitalPlanStatus::AwaitingTransfer => fb::CapitalPlanStatus::AWAITING_TRANSFER,
                CapitalPlanStatus::Reconciling => fb::CapitalPlanStatus::RECONCILING,
                CapitalPlanStatus::Available => fb::CapitalPlanStatus::AVAILABLE,
                CapitalPlanStatus::Completed => fb::CapitalPlanStatus::COMPLETED,
                CapitalPlanStatus::Indeterminate => fb::CapitalPlanStatus::INDETERMINATE,
                CapitalPlanStatus::Rejected => fb::CapitalPlanStatus::REJECTED,
                CapitalPlanStatus::Expired => fb::CapitalPlanStatus::EXPIRED,
                CapitalPlanStatus::Failed => fb::CapitalPlanStatus::FAILED,
            },
            recovery_action: match value.recovery_action {
                CapitalRecoveryAction::None => fb::CapitalRecoveryAction::NONE,
                CapitalRecoveryAction::NoCompensationRequired => {
                    fb::CapitalRecoveryAction::NO_COMPENSATION_REQUIRED
                },
                CapitalRecoveryAction::ReconcileOriginalOperation => {
                    fb::CapitalRecoveryAction::RECONCILE_ORIGINAL_OPERATION
                },
                CapitalRecoveryAction::HoldAndReview => fb::CapitalRecoveryAction::HOLD_AND_REVIEW,
            },
            recovery_reason,
            recovery_decided_at_unix_nanos: value.recovery_decided_at.map(|value| value.get()),
            created_at_unix_nanos: value.created_at.get(),
            expires_at_unix_nanos: value.expires_at.get(),
        },
    )
}

pub(crate) fn operation<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalOperation,
) -> WIPOffset<fb::CapitalOperation<'a>> {
    let operation_id = builder.create_string(value.operation_id.as_str());
    let plan_id = builder.create_string(value.plan_id.as_str());
    let idempotency_key = builder.create_string(value.idempotency_key.as_str());
    let participant_operation_id = value
        .participant_operation_id
        .as_deref()
        .map(|value| builder.create_string(value));
    let participant_state = value
        .participant_state
        .as_deref()
        .map(|value| builder.create_string(value));
    let failure_reason = value
        .failure_reason
        .as_deref()
        .map(|value| builder.create_string(value));
    fb::CapitalOperation::create(
        builder,
        &fb::CapitalOperationArgs {
            operation_id: Some(operation_id),
            plan_id: Some(plan_id),
            idempotency_key: Some(idempotency_key),
            operation_index: value.operation_index,
            kind: match value.kind {
                CapitalOperationKind::Transfer => fb::CapitalOperationKind::TRANSFER,
                CapitalOperationKind::EarnRedemption => fb::CapitalOperationKind::EARN_REDEMPTION,
                CapitalOperationKind::EarnSubscription => {
                    fb::CapitalOperationKind::EARN_SUBSCRIPTION
                },
            },
            status: match value.status {
                CapitalOperationStatus::Prepared => fb::CapitalOperationStatus::PREPARED,
                CapitalOperationStatus::Dispatching => fb::CapitalOperationStatus::DISPATCHING,
                CapitalOperationStatus::AwaitingParticipant => {
                    fb::CapitalOperationStatus::AWAITING_PARTICIPANT
                },
                CapitalOperationStatus::Indeterminate => fb::CapitalOperationStatus::INDETERMINATE,
                CapitalOperationStatus::AwaitingAccountObservation => {
                    fb::CapitalOperationStatus::AWAITING_ACCOUNT_OBSERVATION
                },
                CapitalOperationStatus::Settled => fb::CapitalOperationStatus::SETTLED,
                CapitalOperationStatus::Expired => fb::CapitalOperationStatus::EXPIRED,
                CapitalOperationStatus::Rejected => fb::CapitalOperationStatus::REJECTED,
                CapitalOperationStatus::Failed => fb::CapitalOperationStatus::FAILED,
            },
            participant_operation_id,
            participant_state,
            dispatch_started_at_unix_nanos: value.dispatch_started_at.map(|value| value.get()),
            attempt_count: value.attempt_count,
            failure_reason,
            account_observation_watermark: value
                .account_observation_watermark
                .map(|value| value.get()),
            updated_at_unix_nanos: value.updated_at.get(),
        },
    )
}

fn alert<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &CapitalAlert,
) -> WIPOffset<fb::CapitalAlert<'a>> {
    let alert_id = builder.create_string(&value.alert_id);
    let plan_id = builder.create_string(value.plan_id.as_str());
    let operation_id = value
        .operation_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let message = builder.create_string(&value.message);
    fb::CapitalAlert::create(
        builder,
        &fb::CapitalAlertArgs {
            alert_id: Some(alert_id),
            plan_id: Some(plan_id),
            operation_id,
            kind: match value.kind {
                CapitalAlertKind::ReconciliationRequired => {
                    fb::CapitalAlertKind::RECONCILIATION_REQUIRED
                },
                CapitalAlertKind::ManualReview => fb::CapitalAlertKind::MANUAL_REVIEW,
            },
            severity: match value.severity {
                CapitalAlertSeverity::Warning => fb::CapitalAlertSeverity::WARNING,
                CapitalAlertSeverity::Critical => fb::CapitalAlertSeverity::CRITICAL,
            },
            recovery_action: match value.recovery_action {
                CapitalRecoveryAction::None => fb::CapitalRecoveryAction::NONE,
                CapitalRecoveryAction::NoCompensationRequired => {
                    fb::CapitalRecoveryAction::NO_COMPENSATION_REQUIRED
                },
                CapitalRecoveryAction::ReconcileOriginalOperation => {
                    fb::CapitalRecoveryAction::RECONCILE_ORIGINAL_OPERATION
                },
                CapitalRecoveryAction::HoldAndReview => fb::CapitalRecoveryAction::HOLD_AND_REVIEW,
            },
            message: Some(message),
            opened_at_unix_nanos: value.opened_at.get(),
        },
    )
}

fn as_of(view: &CapitalCurrentView) -> u64 {
    view.availability
        .iter()
        .map(|value| value.evaluated_at.get())
        .chain(view.plans.iter().map(|value| value.created_at.get()))
        .chain(view.operations.iter().map(|value| value.updated_at.get()))
        .chain(view.alerts.iter().map(|value| value.opened_at.get()))
        .max()
        .unwrap_or_default()
}

pub(crate) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

#[cfg(test)]
mod tests {
    use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
    use kairos_primitives::capital::{CapitalGroupId, CapitalOperationId, CapitalPlanId};
    use kairos_primitives::reference::Currency;
    use kairos_primitives::runtime::StrategyId;
    use kairos_primitives::time::{Generation, Sequence, UnixNanos};

    use super::*;

    #[test]
    fn current_view_round_trips_through_the_flatbuffer_root() {
        let key = CapitalViewKey::current("group-1");
        let mut writer = FlatbuffersCapitalViewWriter::new(
            "capital",
            InstanceIdentity::new("workspace", "launch", "instance").unwrap(),
            key,
        );
        writer
            .publish(&CapitalCurrentView {
                capital_group_id: CapitalGroupId::new("group-1").unwrap(),
                strategy_id: StrategyId::new("strategy-1").unwrap(),
                environment: "paper".into(),
                membership_version: Generation::new(1),
                event_sequence: Sequence::new(2),
                journal_sequence: Sequence::new(3),
                objectives: vec![],
                demands: vec![],
                policies: vec![],
                facts: vec![],
                availability: vec![CapitalAvailability {
                    destination: FundingLocation {
                        broker: BrokerId::new("paper").unwrap(),
                        account_id: AccountId::new("main").unwrap(),
                        segment: SegmentKey::new("spot").unwrap(),
                        asset: Currency::new("USDT").unwrap(),
                    },
                    readiness: CapitalReadiness::Ready,
                    policy_version: Generation::new(1),
                    active_objective_ids: vec![],
                    active_demand_ids: vec![],
                    funding_horizons: vec![],
                    desired_target: "10".parse().unwrap(),
                    effective_target: "10".parse().unwrap(),
                    observed_available: "4".parse().unwrap(),
                    deficit: "6".parse().unwrap(),
                    deficit_observed_since: None,
                    cooldown_until: None,
                    account_watermark: Sequence::new(7),
                    risk_policy_version: Generation::new(8),
                    risk_watermark: Sequence::new(9),
                    evaluated_at: 10.into(),
                    reason: None,
                }],
                routes: vec![],
                plans: vec![],
                reservations: vec![],
                operations: vec![],
                alerts: vec![CapitalAlert {
                    alert_id: kairos_primitives::runtime::EventId::new("capital-recovery:plan-1")
                        .unwrap(),
                    plan_id: CapitalPlanId::new("plan-1").unwrap(),
                    operation_id: Some(CapitalOperationId::new("operation-1").unwrap()),
                    kind: CapitalAlertKind::ReconciliationRequired,
                    severity: CapitalAlertSeverity::Warning,
                    recovery_action: CapitalRecoveryAction::ReconcileOriginalOperation,
                    message: "query the original operation".into(),
                    opened_at: UnixNanos::new(11),
                }],
            })
            .unwrap();
        let payload = writer.last_payload.unwrap();
        assert!(fb::capital_current_view_buffer_has_identifier(&payload));
        let root = fb::root_as_capital_current_view(&payload).unwrap();
        let state = root.state();
        assert_eq!(state.capital_group_id(), "group-1");
        assert_eq!(state.availability().len(), 1);
        assert_eq!(state.availability().get(0).deficit().mantissa(), 6);
        assert_eq!(state.alerts().len(), 1);
        assert_eq!(state.alerts().get(0).plan_id(), "plan-1");
        assert_eq!(
            state.alerts().get(0).recovery_action(),
            fb::CapitalRecoveryAction::RECONCILE_ORIGINAL_OPERATION
        );
    }
}
