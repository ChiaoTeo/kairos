use flatbuffers::{FlatBufferBuilder, WIPOffset};
use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;

use crate::{
    CapitalAlert, CapitalAlertKind, CapitalAlertSeverity, CapitalAvailability,
    CapitalCurrentRecords, CapitalDemand, CapitalDemandLifecycleStatus, CapitalEarnHolding,
    CapitalFacts, CapitalFundingHorizon, CapitalOperation, CapitalOperationKind,
    CapitalOperationStatus, CapitalPlan, CapitalPlanStatus, CapitalPolicy, CapitalReadiness,
    CapitalRecoveryAction, CapitalReservation, CapitalReservationStatus, CapitalRoute,
    CapitalRouteKind, CapitalSettlementClass, ContractResult, FundingLocation, FundingObjective,
    FundingObjectiveLifecycleStatus, FundingPriority,
};

pub fn encode_indexed_current(
    view: &CapitalCurrentRecords,
) -> ContractResult<std::collections::BTreeMap<(String, Vec<u8>), Vec<u8>>> {
    let mut values = std::collections::BTreeMap::new();
    {
        let mut builder = FlatBufferBuilder::new();
        let group_id = builder.create_string(view.capital_group_id.as_str());
        let strategy_id = builder.create_string(view.strategy_id.as_str());
        let environment = builder.create_string(&view.environment);
        let root = fb::CapitalStateCurrent::create(
            &mut builder,
            &fb::CapitalStateCurrentArgs {
                capital_group_id: Some(group_id),
                strategy_id: Some(strategy_id),
                environment: Some(environment),
                membership_version: view.membership_version.get(),
                event_sequence: view.event_sequence.get(),
                journal_sequence: view.journal_sequence.get(),
            },
        );
        fb::finish_capital_state_current_buffer(&mut builder, root);
        values.insert(
            (
                crate::CAPITAL_STATE_DATABASE.to_owned(),
                crate::capital_indexed_key(&[view.capital_group_id.as_str()])?,
            ),
            builder.finished_data().to_vec(),
        );
    }

    macro_rules! entity {
        ($database:expr, $key:expr, $root:ident, $args:ident, $finish:ident, $encoder:ident, $value:expr) => {{
            let mut builder = FlatBufferBuilder::new();
            let entity = $encoder(&mut builder, $value);
            let group_id = builder.create_string(view.capital_group_id.as_str());
            let root = fb::$root::create(
                &mut builder,
                &fb::$args {
                    capital_group_id: Some(group_id),
                    value: Some(entity),
                },
            );
            fb::$finish(&mut builder, root);
            values.insert(
                ($database.to_owned(), $key),
                builder.finished_data().to_vec(),
            );
        }};
    }

    for value in &view.objectives {
        entity!(
            crate::CAPITAL_OBJECTIVES_DATABASE,
            crate::capital_indexed_key(&[value.objective_id.as_str()])?,
            CapitalObjectiveCurrent,
            CapitalObjectiveCurrentArgs,
            finish_capital_objective_current_buffer,
            objective,
            value
        );
    }
    for value in &view.demands {
        entity!(
            crate::CAPITAL_DEMANDS_DATABASE,
            crate::capital_indexed_key(&[value.demand_id.as_str()])?,
            CapitalDemandCurrent,
            CapitalDemandCurrentArgs,
            finish_capital_demand_current_buffer,
            demand,
            value
        );
    }
    for value in &view.policies {
        entity!(
            crate::CAPITAL_POLICIES_DATABASE,
            crate::location_key(&value.destination)?,
            CapitalPolicyCurrent,
            CapitalPolicyCurrentArgs,
            finish_capital_policy_current_buffer,
            policy,
            value
        );
    }
    for value in &view.facts {
        entity!(
            crate::CAPITAL_FACTS_DATABASE,
            crate::location_key(&value.destination)?,
            CapitalFactsCurrent,
            CapitalFactsCurrentArgs,
            finish_capital_facts_current_buffer,
            capital_facts,
            value
        );
    }
    for value in &view.availability {
        entity!(
            crate::CAPITAL_AVAILABILITY_DATABASE,
            crate::location_key(&value.destination)?,
            CapitalAvailabilityCurrent,
            CapitalAvailabilityCurrentArgs,
            finish_capital_availability_current_buffer,
            availability,
            value
        );
    }
    for value in &view.routes {
        entity!(
            crate::CAPITAL_ROUTES_DATABASE,
            crate::capital_indexed_key(&[value.route_id.as_str()])?,
            CapitalRouteCurrent,
            CapitalRouteCurrentArgs,
            finish_capital_route_current_buffer,
            route,
            value
        );
    }
    for value in &view.plans {
        entity!(
            crate::CAPITAL_PLANS_DATABASE,
            crate::capital_indexed_key(&[value.plan_id.as_str()])?,
            CapitalPlanCurrent,
            CapitalPlanCurrentArgs,
            finish_capital_plan_current_buffer,
            plan,
            value
        );
    }
    for value in &view.reservations {
        entity!(
            crate::CAPITAL_RESERVATIONS_DATABASE,
            crate::capital_indexed_key(&[value.reservation_id.as_str()])?,
            CapitalReservationCurrent,
            CapitalReservationCurrentArgs,
            finish_capital_reservation_current_buffer,
            reservation,
            value
        );
    }
    for value in &view.operations {
        entity!(
            crate::CAPITAL_OPERATIONS_DATABASE,
            crate::capital_indexed_key(&[value.operation_id.as_str()])?,
            CapitalOperationCurrent,
            CapitalOperationCurrentArgs,
            finish_capital_operation_current_buffer,
            operation,
            value
        );
    }
    for value in &view.alerts {
        entity!(
            crate::CAPITAL_ALERTS_DATABASE,
            crate::capital_indexed_key(&[value.alert_id.as_str()])?,
            CapitalAlertCurrent,
            CapitalAlertCurrentArgs,
            finish_capital_alert_current_buffer,
            alert,
            value
        );
    }

    Ok(values)
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

pub(crate) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}
