use kairos_capital_contract as contract;

use crate::{
    CapitalAvailabilityView, CapitalDemandRecord, CapitalDemandStatus, CapitalFacts,
    CapitalOperation, CapitalOperationKind, CapitalOperationStatus, CapitalPlan, CapitalPlanStatus,
    CapitalPolicy, CapitalReadiness, CapitalReservation, CapitalReservationStatus,
    CapitalRouteKind, CapitalSettlementClass, CapitalSnapshot, CapitalTransferRoute,
    FundingLocation, FundingObjectiveRecord, FundingObjectiveStatus, FundingPriority,
};

pub fn capital_current_view(snapshot: &CapitalSnapshot) -> contract::CapitalCurrentView {
    contract::CapitalCurrentView {
        capital_group_id: snapshot.capital_group_id.clone(),
        strategy_id: snapshot.strategy_id.clone(),
        environment: snapshot.environment.clone(),
        membership_version: snapshot.membership_version,
        event_sequence: snapshot.event_sequence,
        journal_sequence: snapshot.journal_sequence,
        objectives: snapshot.objectives.iter().map(objective).collect(),
        demands: snapshot.demands.iter().map(demand).collect(),
        policies: snapshot.policies.iter().map(policy).collect(),
        facts: snapshot.facts.iter().map(facts).collect(),
        availability: snapshot.availability.iter().map(availability).collect(),
        routes: snapshot.routes.iter().map(route).collect(),
        plans: snapshot.plans.iter().map(plan).collect(),
        reservations: snapshot.reservations.iter().map(reservation).collect(),
        operations: snapshot.operations.iter().map(operation).collect(),
    }
}

pub fn capital_event(value: &crate::CapitalEvent) -> contract::CapitalEvent {
    match value {
        crate::CapitalEvent::FundingObjectiveChanged {
            record,
            event_sequence,
        } => contract::CapitalEvent::FundingObjectiveChanged {
            objective: objective(record),
            event_sequence: *event_sequence,
            occurred_at: record.updated_at,
        },
        crate::CapitalEvent::CapitalDemandChanged {
            record,
            event_sequence,
        } => contract::CapitalEvent::CapitalDemandChanged {
            demand: demand(record),
            event_sequence: *event_sequence,
            occurred_at: record.updated_at,
        },
        crate::CapitalEvent::PolicyChanged {
            policy: value,
            event_sequence,
            occurred_at,
        } => contract::CapitalEvent::PolicyChanged {
            policy: policy(value),
            event_sequence: *event_sequence,
            occurred_at: *occurred_at,
        },
        crate::CapitalEvent::FactsObserved {
            facts: value,
            event_sequence,
        } => contract::CapitalEvent::FactsObserved {
            facts: facts(value),
            event_sequence: *event_sequence,
            occurred_at: value.account_observed_at,
        },
        crate::CapitalEvent::AvailabilityEvaluated {
            availability: values,
            event_sequence,
        } => contract::CapitalEvent::AvailabilityEvaluated {
            availability: values.iter().map(availability).collect(),
            event_sequence: *event_sequence,
            occurred_at: values
                .iter()
                .map(|value| value.evaluated_at)
                .max()
                .unwrap_or_default(),
        },
        crate::CapitalEvent::RouteChanged {
            route: value,
            event_sequence,
            occurred_at,
        } => contract::CapitalEvent::RouteChanged {
            route: route(value),
            event_sequence: *event_sequence,
            occurred_at: *occurred_at,
        },
        crate::CapitalEvent::PlanAuthorized {
            plan: plan_value,
            reservation: reservation_value,
            event_sequence,
        } => contract::CapitalEvent::PlanAuthorized {
            plan: plan(plan_value),
            reservation: reservation(reservation_value),
            event_sequence: *event_sequence,
            occurred_at: plan_value.created_at,
        },
        crate::CapitalEvent::PlanStateChanged {
            plan: plan_value,
            reservation: reservation_value,
            operation: operation_value,
            event_sequence,
        } => contract::CapitalEvent::PlanStateChanged {
            plan: plan(plan_value),
            reservation: reservation(reservation_value),
            operation: operation(operation_value),
            event_sequence: *event_sequence,
            occurred_at: operation_value.updated_at,
        },
        crate::CapitalEvent::PlanExpired {
            plan: plan_value,
            reservation: reservation_value,
            operation: operation_value,
            event_sequence,
        } => contract::CapitalEvent::PlanExpired {
            plan: plan(plan_value),
            reservation: reservation(reservation_value),
            operation: operation_value.as_deref().map(operation),
            event_sequence: *event_sequence,
            occurred_at: operation_value
                .as_deref()
                .map(|value| value.updated_at)
                .unwrap_or(plan_value.expires_at),
        },
    }
}

fn priority(value: FundingPriority) -> contract::FundingPriority {
    match value {
        FundingPriority::Low => contract::FundingPriority::Low,
        FundingPriority::Normal => contract::FundingPriority::Normal,
        FundingPriority::High => contract::FundingPriority::High,
        FundingPriority::Critical => contract::FundingPriority::Critical,
    }
}

fn objective(value: &FundingObjectiveRecord) -> contract::FundingObjective {
    contract::FundingObjective {
        objective_id: value.objective.objective_id.clone(),
        version: value.objective.version,
        strategy_id: value.objective.strategy_id.clone(),
        destination: location(&value.objective.destination),
        desired_available: value.objective.desired_available,
        required_by: value.objective.required_by,
        expires_at: value.objective.expires_at,
        priority: priority(value.objective.priority),
        confidence_bps: value.objective.confidence_bps,
        strategy_decision_id: value.objective.strategy_decision_id.clone(),
        status: match value.status {
            FundingObjectiveStatus::Active => contract::FundingObjectiveLifecycleStatus::Active,
            FundingObjectiveStatus::Cancelled => {
                contract::FundingObjectiveLifecycleStatus::Cancelled
            },
            FundingObjectiveStatus::Expired => contract::FundingObjectiveLifecycleStatus::Expired,
        },
        updated_at: value.updated_at,
    }
}

fn demand(value: &CapitalDemandRecord) -> contract::CapitalDemand {
    contract::CapitalDemand {
        demand_id: value.demand.demand_id.clone(),
        idempotency_key: value.demand.idempotency_key.clone(),
        strategy_id: value.demand.strategy_id.clone(),
        destination: location(&value.demand.destination),
        observed_shortfall: value.demand.observed_shortfall,
        observed_at: value.demand.observed_at,
        required_by: value.demand.required_by,
        expires_at: value.demand.expires_at,
        priority: priority(value.demand.priority),
        confidence_bps: value.demand.confidence_bps,
        account_watermark: value.demand.account_watermark,
        risk_watermark: value.demand.risk_watermark,
        launch_id: value.demand.launch_id.clone(),
        instance_id: value.demand.instance_id.clone(),
        causal_references: value.demand.causal_references.clone(),
        status: match value.status {
            CapitalDemandStatus::Active => contract::CapitalDemandLifecycleStatus::Active,
            CapitalDemandStatus::Expired => contract::CapitalDemandLifecycleStatus::Expired,
        },
        updated_at: value.updated_at,
    }
}

fn policy(value: &CapitalPolicy) -> contract::CapitalPolicy {
    contract::CapitalPolicy {
        destination: location(&value.destination),
        version: value.version,
        minimum: value.minimum,
        default_target: value.default_target,
        maximum: value.maximum,
        stress_buffer: value.stress_buffer,
        minimum_movement: value.minimum_movement,
        hysteresis: value.hysteresis,
        deficit_dwell_nanos: value.deficit_dwell_nanos,
        cooldown_nanos: value.cooldown_nanos,
        max_fact_age_nanos: value.max_fact_age_nanos,
    }
}

fn facts(value: &CapitalFacts) -> contract::CapitalFacts {
    contract::CapitalFacts {
        destination: location(&value.destination),
        observed_available: value.observed_available,
        account_watermark: value.account_watermark,
        account_observed_at: value.account_observed_at,
        account_complete: value.account_complete,
        risk_capacity: value.risk_capacity,
        risk_policy_version: value.risk_policy_version,
        risk_watermark: value.risk_watermark,
        earn_holdings: value
            .earn_holdings
            .iter()
            .map(|holding| contract::CapitalEarnHolding {
                product_id: holding.product_id.clone(),
                principal: holding.principal,
                redeemable_amount: holding.redeemable_amount,
                immediately_redeemable: holding.immediately_redeemable,
                active: holding.active,
            })
            .collect(),
    }
}

fn route(value: &CapitalTransferRoute) -> contract::CapitalRoute {
    contract::CapitalRoute {
        route_id: value.route_id.clone(),
        version: value.version,
        source: location(&value.source),
        destination: location(&value.destination),
        kind: match value.kind {
            CapitalRouteKind::InternalTransfer => contract::CapitalRouteKind::InternalTransfer,
            CapitalRouteKind::AccountTransfer => contract::CapitalRouteKind::AccountTransfer,
            CapitalRouteKind::EarnRedemptionThenTransfer => {
                contract::CapitalRouteKind::EarnRedemptionThenTransfer
            },
            CapitalRouteKind::EarnSubscription => contract::CapitalRouteKind::EarnSubscription,
        },
        per_operation_limit: value.per_operation_limit,
        daily_limit: value.daily_limit,
        required_source_authority: value.required_source_authority.clone(),
        settlement_class: match value.settlement_class {
            CapitalSettlementClass::ImmediateBookTransfer => {
                contract::CapitalSettlementClass::ImmediateBookTransfer
            },
            CapitalSettlementClass::ParticipantHistoryThenAccountObservation => {
                contract::CapitalSettlementClass::ParticipantHistoryThenAccountObservation
            },
        },
        enabled: value.enabled,
        earn_product_id: value.earn_product_id.clone(),
        demand_guard_nanos: value.demand_guard_nanos,
        allow_unknown_redemption_quota: value.allow_unknown_redemption_quota,
    }
}

fn reservation(value: &CapitalReservation) -> contract::CapitalReservation {
    contract::CapitalReservation {
        reservation_id: value.reservation_id.clone(),
        plan_id: value.plan_id.clone(),
        source: location(&value.source),
        amount: value.amount,
        source_account_watermark: value.source_account_watermark,
        status: match value.status {
            CapitalReservationStatus::Active => contract::CapitalReservationStatus::Active,
            CapitalReservationStatus::Consumed => contract::CapitalReservationStatus::Consumed,
            CapitalReservationStatus::Released => contract::CapitalReservationStatus::Released,
            CapitalReservationStatus::Expired => contract::CapitalReservationStatus::Expired,
        },
        created_at: value.created_at,
        expires_at: value.expires_at,
    }
}

fn location(value: &FundingLocation) -> contract::FundingLocation {
    contract::FundingLocation {
        broker: value.broker.clone(),
        account_id: value.account_id.clone(),
        segment: value.segment.clone(),
        asset: value.asset.clone(),
    }
}

fn availability(value: &CapitalAvailabilityView) -> contract::CapitalAvailability {
    contract::CapitalAvailability {
        destination: location(&value.destination),
        readiness: match value.readiness {
            CapitalReadiness::WaitingForFacts => contract::CapitalReadiness::WaitingForFacts,
            CapitalReadiness::Degraded => contract::CapitalReadiness::Degraded,
            CapitalReadiness::Ready => contract::CapitalReadiness::Ready,
        },
        policy_version: value.policy_version,
        active_objective_ids: value.active_objective_ids.clone(),
        active_demand_ids: value.active_demand_ids.clone(),
        funding_horizons: value
            .funding_horizons
            .iter()
            .map(|horizon| contract::CapitalFundingHorizon {
                required_by: horizon.required_by,
                objective_ids: horizon.objective_ids.clone(),
                demand_ids: horizon.demand_ids.clone(),
                desired_available: horizon.desired_available,
            })
            .collect(),
        desired_target: value.desired_target,
        effective_target: value.effective_target,
        observed_available: value.observed_available,
        deficit: value.deficit,
        deficit_observed_since: value.deficit_observed_since,
        cooldown_until: value.cooldown_until,
        account_watermark: value.account_watermark,
        risk_policy_version: value.risk_policy_version,
        risk_watermark: value.risk_watermark,
        evaluated_at: value.evaluated_at,
        reason: value.reason.clone(),
    }
}

fn plan(value: &CapitalPlan) -> contract::CapitalPlan {
    contract::CapitalPlan {
        plan_id: value.plan_id.clone(),
        rebalance_decision_id: value.rebalance_decision_id.clone(),
        route_id: value.route_id.clone(),
        route_version: value.route_version,
        route_kind: match value.route_kind {
            CapitalRouteKind::InternalTransfer => contract::CapitalRouteKind::InternalTransfer,
            CapitalRouteKind::AccountTransfer => contract::CapitalRouteKind::AccountTransfer,
            CapitalRouteKind::EarnRedemptionThenTransfer => {
                contract::CapitalRouteKind::EarnRedemptionThenTransfer
            },
            CapitalRouteKind::EarnSubscription => contract::CapitalRouteKind::EarnSubscription,
        },
        source: location(&value.source),
        destination: location(&value.destination),
        amount: value.amount,
        objective_ids: value.objective_ids.clone(),
        demand_ids: value.demand_ids.clone(),
        reservation_id: value.reservation_id.clone(),
        idempotency_key: value.idempotency_key.clone(),
        selected_earn_product_id: value.selected_earn_product_id.clone(),
        source_account_watermark: value.source_account_watermark,
        destination_account_watermark: value.destination_account_watermark,
        source_observed_available: value.source_observed_available,
        destination_observed_available: value.destination_observed_available,
        redemption_account_watermark: value.redemption_account_watermark,
        redemption_observed_available: value.redemption_observed_available,
        earn_principal_before: value.earn_principal_before,
        status: match value.status {
            CapitalPlanStatus::Authorized => contract::CapitalPlanStatus::Authorized,
            CapitalPlanStatus::Redeeming => contract::CapitalPlanStatus::Redeeming,
            CapitalPlanStatus::AwaitingRedemption => {
                contract::CapitalPlanStatus::AwaitingRedemption
            },
            CapitalPlanStatus::Transferring => contract::CapitalPlanStatus::Transferring,
            CapitalPlanStatus::AwaitingTransfer => contract::CapitalPlanStatus::AwaitingTransfer,
            CapitalPlanStatus::Subscribing => contract::CapitalPlanStatus::Subscribing,
            CapitalPlanStatus::AwaitingSubscription => {
                contract::CapitalPlanStatus::AwaitingSubscription
            },
            CapitalPlanStatus::Reconciling => contract::CapitalPlanStatus::Reconciling,
            CapitalPlanStatus::Available => contract::CapitalPlanStatus::Available,
            CapitalPlanStatus::Completed => contract::CapitalPlanStatus::Completed,
            CapitalPlanStatus::Indeterminate => contract::CapitalPlanStatus::Indeterminate,
            CapitalPlanStatus::Rejected => contract::CapitalPlanStatus::Rejected,
            CapitalPlanStatus::Expired => contract::CapitalPlanStatus::Expired,
            CapitalPlanStatus::Failed => contract::CapitalPlanStatus::Failed,
        },
        created_at: value.created_at,
        expires_at: value.expires_at,
    }
}

fn operation(value: &CapitalOperation) -> contract::CapitalOperation {
    contract::CapitalOperation {
        operation_id: value.operation_id.clone(),
        plan_id: value.plan_id.clone(),
        idempotency_key: value.idempotency_key.clone(),
        operation_index: value.operation_index,
        kind: match value.kind {
            CapitalOperationKind::Transfer => contract::CapitalOperationKind::Transfer,
            CapitalOperationKind::EarnRedemption => contract::CapitalOperationKind::EarnRedemption,
            CapitalOperationKind::EarnSubscription => {
                contract::CapitalOperationKind::EarnSubscription
            },
        },
        status: match value.status {
            CapitalOperationStatus::Prepared => contract::CapitalOperationStatus::Prepared,
            CapitalOperationStatus::Dispatching => contract::CapitalOperationStatus::Dispatching,
            CapitalOperationStatus::AwaitingParticipant => {
                contract::CapitalOperationStatus::AwaitingParticipant
            },
            CapitalOperationStatus::Indeterminate => {
                contract::CapitalOperationStatus::Indeterminate
            },
            CapitalOperationStatus::AwaitingAccountObservation => {
                contract::CapitalOperationStatus::AwaitingAccountObservation
            },
            CapitalOperationStatus::Settled => contract::CapitalOperationStatus::Settled,
            CapitalOperationStatus::Expired => contract::CapitalOperationStatus::Expired,
            CapitalOperationStatus::Rejected => contract::CapitalOperationStatus::Rejected,
            CapitalOperationStatus::Failed => contract::CapitalOperationStatus::Failed,
        },
        participant_operation_id: value.participant_operation_id.clone(),
        participant_state: value.participant_state.clone(),
        dispatch_started_at: value.dispatch_started_at,
        attempt_count: value.attempt_count,
        failure_reason: value.failure_reason.clone(),
        account_observation_watermark: value.account_observation_watermark,
        updated_at: value.updated_at,
    }
}
