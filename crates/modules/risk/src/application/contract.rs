//! Explicit Risk domain-to-Contract mappings.
//!
//! These mappings are shared by Conflux output turns and concrete composition
//! tests. Event and view publication never use a JSON round trip.

fn amount(value: crate::Amount) -> kairos_risk_contract::Amount {
    kairos_risk_contract::Amount::new(value.mantissa(), value.scale())
        .expect("Risk domain amount satisfies contract decimal bounds")
}

fn money(value: kairos_primitives::Money) -> kairos_risk_contract::Amount {
    kairos_risk_contract::Amount::new(value.mantissa(), value.scale())
        .expect("Money satisfies contract decimal bounds")
}

fn metric(value: crate::Metric) -> kairos_risk_contract::Metric {
    use kairos_risk_contract::Metric as Wire;

    use crate::Metric as Domain;
    match value {
        Domain::Notional => Wire::Notional,
        Domain::Margin => Wire::Margin,
        Domain::GrossExposure => Wire::GrossExposure,
        Domain::NetExposure => Wire::NetExposure,
        Domain::Turnover => Wire::Turnover,
        Domain::OrderRate => Wire::OrderRate,
        Domain::DailyLoss => Wire::DailyLoss,
        Domain::Drawdown => Wire::Drawdown,
        Domain::Leverage => Wire::Leverage,
        Domain::PriceDeviation => Wire::PriceDeviation,
        Domain::StressLoss => Wire::StressLoss,
    }
}

fn policy(value: &crate::RiskPolicy) -> kairos_risk_contract::RiskPolicy {
    kairos_risk_contract::RiskPolicy {
        policy_id: value.policy_id.clone(),
        version: value.version,
        scope: kairos_risk_contract::PolicyScope {
            account_id: value.scope.account_id.clone(),
            strategy_id: value.scope.strategy_id.clone(),
            instrument_id: value.scope.instrument_id.clone(),
            exchange_id: value.scope.exchange_id.clone(),
        },
        metric: metric(value.metric),
        limit: amount(value.limit),
        enforcement: match value.enforcement {
            crate::EnforcementMode::Reject => kairos_risk_contract::EnforcementMode::Reject,
            crate::EnforcementMode::Warn => kairos_risk_contract::EnforcementMode::Warn,
            crate::EnforcementMode::Observe => kairos_risk_contract::EnforcementMode::Observe,
        },
        valid_from_unix_nanos: value.valid_from_unix_nanos,
        valid_until_unix_nanos: value.valid_until_unix_nanos,
        window_nanos: value.window_nanos,
    }
}

fn allocation(value: &crate::Allocation) -> kairos_risk_contract::Allocation {
    kairos_risk_contract::Allocation {
        policy_id: value.policy_id.clone(),
        metric: metric(value.metric),
        amount: amount(value.amount),
    }
}

pub(crate) fn reservation(value: &crate::Reservation) -> kairos_risk_contract::Reservation {
    kairos_risk_contract::Reservation {
        reservation_id: value.reservation_id.clone(),
        request_id: value.request_id.clone(),
        account_id: value.account_id.clone(),
        strategy_id: value.strategy_id.clone(),
        idempotency_key: value.idempotency_key.clone(),
        allocations: value.allocations.iter().map(allocation).collect(),
        status: match value.status {
            crate::ReservationStatus::Reserved => kairos_risk_contract::ReservationStatus::Reserved,
            crate::ReservationStatus::Consumed => kairos_risk_contract::ReservationStatus::Consumed,
            crate::ReservationStatus::Released => kairos_risk_contract::ReservationStatus::Released,
            crate::ReservationStatus::Expired => kairos_risk_contract::ReservationStatus::Expired,
        },
        created_at_unix_nanos: value.created_at_unix_nanos,
        updated_at_unix_nanos: value.updated_at_unix_nanos,
        expires_at_unix_nanos: value.expires_at_unix_nanos,
        policy_version: value.policy_version,
    }
}

pub(crate) fn circuit(value: &crate::CircuitState) -> kairos_risk_contract::CircuitState {
    kairos_risk_contract::CircuitState {
        scope: kairos_risk_contract::CircuitScope {
            account_id: value.scope.account_id.clone(),
            strategy_id: value.scope.strategy_id.clone(),
            exchange_id: value.scope.exchange_id.clone(),
        },
        open: value.open,
        opened_at_unix_nanos: value.opened_at_unix_nanos,
        reset_at_unix_nanos: value.reset_at_unix_nanos,
        reason: value.reason.clone(),
    }
}

fn context(value: &crate::RiskContext) -> kairos_risk_contract::RiskContext {
    kairos_risk_contract::RiskContext {
        account_snapshot_watermark: value.account_snapshot_watermark,
        market_freshness_watermark: value.market_freshness_watermark,
        portfolio_version: value.portfolio_version,
        current_exposure: amount(value.current_exposure),
        current_margin: amount(value.current_margin),
        available_margin: amount(value.available_margin),
        current_pnl: money(value.current_pnl),
        current_drawdown: amount(value.current_drawdown),
        market_is_fresh: value.market_is_fresh,
        leverage_bps: value.leverage_bps,
        price_deviation_bps: value.price_deviation_bps,
        stress_loss: amount(value.stress_loss),
    }
}

fn reason(value: &crate::ReasonCode) -> kairos_risk_contract::ReasonCode {
    use kairos_risk_contract::ReasonCode as Wire;

    use crate::ReasonCode as Domain;
    match value {
        Domain::NoMatchingPolicy => Wire::NoMatchingPolicy,
        Domain::LimitExceeded => Wire::LimitExceeded,
        Domain::StaleDependency => Wire::StaleDependency,
        Domain::DuplicateRequest => Wire::DuplicateRequest,
        Domain::ReservationNotFound => Wire::ReservationNotFound,
        Domain::ReservationNotActive => Wire::ReservationNotActive,
        Domain::InvalidRequest => Wire::InvalidRequest,
        Domain::PersistenceFailure => Wire::PersistenceFailure,
        Domain::CircuitOpen => Wire::CircuitOpen,
        Domain::StaleMarket => Wire::StaleMarket,
        Domain::InsufficientMargin => Wire::InsufficientMargin,
        Domain::LeverageExceeded => Wire::LeverageExceeded,
        Domain::LossLimitExceeded => Wire::LossLimitExceeded,
    }
}

pub(crate) fn decision(
    value: &crate::RiskDecision,
    account_id: &kairos_primitives::AccountId,
    strategy_id: &kairos_primitives::StrategyId,
    instrument_id: Option<&kairos_primitives::InstrumentId>,
) -> kairos_risk_contract::RiskDecision {
    kairos_risk_contract::RiskDecision {
        decision_id: value.decision_id.clone(),
        request_id: value.request_id.clone(),
        account_id: account_id.clone(),
        strategy_id: strategy_id.clone(),
        instrument_id: instrument_id.cloned(),
        allowed: value.allowed,
        degraded: value.degraded,
        reason_codes: value.reason_codes.iter().map(reason).collect(),
        violations: value.violations.clone(),
        allocations: value.allocations.iter().map(allocation).collect(),
        reservation: value.reservation.as_ref().map(reservation),
        policy_version: value.policy_version,
        dependency_watermarks: kairos_risk_contract::DependencyWatermarks {
            generation: value.dependency_watermarks.generation,
            event_sequence: value.dependency_watermarks.event_sequence,
        },
        context: value.context.as_ref().map(context),
        funding_requirement: value.funding_requirement.as_ref().map(|requirement| {
            kairos_risk_contract::FundingRequirement {
                required_margin: amount(requirement.required_margin),
                available_margin: amount(requirement.available_margin),
                shortfall: amount(requirement.shortfall),
                margin_rule_id: requirement.margin_rule_id.clone(),
                account_segment: requirement.account_segment.clone(),
                collateral_asset: requirement.collateral_asset.clone(),
            }
        }),
        evaluated_at_unix_nanos: value.evaluated_at_unix_nanos,
    }
}

pub(crate) fn policy_from(
    value: kairos_risk_contract::RiskPolicy,
) -> Result<crate::RiskPolicy, String> {
    let scope = value.scope;
    Ok(crate::RiskPolicy {
        policy_id: value.policy_id,
        version: value.version,
        scope: crate::PolicyScope {
            account_id: scope.account_id,
            strategy_id: scope.strategy_id,
            instrument_id: scope.instrument_id,
            exchange_id: scope.exchange_id,
        },
        metric: metric_from(value.metric),
        limit: amount_from(value.limit)?,
        enforcement: match value.enforcement {
            kairos_risk_contract::EnforcementMode::Reject => crate::EnforcementMode::Reject,
            kairos_risk_contract::EnforcementMode::Warn => crate::EnforcementMode::Warn,
            kairos_risk_contract::EnforcementMode::Observe => crate::EnforcementMode::Observe,
        },
        valid_from_unix_nanos: value.valid_from_unix_nanos,
        valid_until_unix_nanos: value.valid_until_unix_nanos,
        window_nanos: value.window_nanos,
    })
}

pub(crate) fn authorize_from(
    value: kairos_risk_contract::AuthorizeRequest,
) -> Result<crate::domain::AuthorizeRequest, String> {
    Ok(crate::domain::AuthorizeRequest {
        request_id: value.request_id,
        idempotency_key: value.idempotency_key,
        reservation_id: value.reservation_id,
        account_id: value.account_id,
        strategy_id: value.strategy_id,
        instrument_id: value.instrument_id,
        exchange_id: value.exchange_id,
        proposal: crate::TradeRiskProposal {
            notional: amount_from(value.proposal.notional)?,
            initial_margin_rate_bps: value.proposal.initial_margin_rate_bps,
            account_segment: value.proposal.account_segment,
            collateral_asset: value.proposal.collateral_asset,
            reduce_only: value.proposal.reduce_only,
            margin_rule_id: value.proposal.margin_rule_id,
        },
        at_unix_nanos: value.at_unix_nanos,
        reservation_ttl_nanos: value.reservation_ttl_nanos,
        dependency_generation: value.dependency_generation,
        dependency_event_sequence: value.dependency_event_sequence,
        context: value.context.map(context_from).transpose()?,
    })
}

pub(crate) fn circuit_scope_from(
    value: kairos_risk_contract::CircuitScope,
) -> Result<crate::CircuitScope, String> {
    Ok(crate::CircuitScope {
        account_id: value.account_id,
        strategy_id: value.strategy_id,
        exchange_id: value.exchange_id,
    })
}

pub(crate) fn amount_from(value: kairos_risk_contract::Amount) -> Result<crate::Amount, String> {
    crate::Amount::new(value.mantissa(), value.scale())
}

fn metric_from(value: kairos_risk_contract::Metric) -> crate::Metric {
    match value {
        kairos_risk_contract::Metric::Notional => crate::Metric::Notional,
        kairos_risk_contract::Metric::Margin => crate::Metric::Margin,
        kairos_risk_contract::Metric::GrossExposure => crate::Metric::GrossExposure,
        kairos_risk_contract::Metric::NetExposure => crate::Metric::NetExposure,
        kairos_risk_contract::Metric::Turnover => crate::Metric::Turnover,
        kairos_risk_contract::Metric::OrderRate => crate::Metric::OrderRate,
        kairos_risk_contract::Metric::DailyLoss => crate::Metric::DailyLoss,
        kairos_risk_contract::Metric::Drawdown => crate::Metric::Drawdown,
        kairos_risk_contract::Metric::Leverage => crate::Metric::Leverage,
        kairos_risk_contract::Metric::PriceDeviation => crate::Metric::PriceDeviation,
        kairos_risk_contract::Metric::StressLoss => crate::Metric::StressLoss,
    }
}

fn context_from(value: kairos_risk_contract::RiskContext) -> Result<crate::RiskContext, String> {
    Ok(crate::RiskContext {
        account_snapshot_watermark: value.account_snapshot_watermark,
        market_freshness_watermark: value.market_freshness_watermark,
        portfolio_version: value.portfolio_version,
        current_exposure: amount_from(value.current_exposure)?,
        current_margin: amount_from(value.current_margin)?,
        available_margin: amount_from(value.available_margin)?,
        current_pnl: kairos_primitives::Money::new(
            value.current_pnl.mantissa(),
            value.current_pnl.scale(),
        )
        .map_err(|e| e.to_string())?,
        current_drawdown: amount_from(value.current_drawdown)?,
        market_is_fresh: value.market_is_fresh,
        leverage_bps: value.leverage_bps,
        price_deviation_bps: value.price_deviation_bps,
        stress_loss: amount_from(value.stress_loss)?,
    })
}

pub(crate) fn current_view(
    value: &crate::RiskCurrentView,
) -> kairos_risk_contract::RiskCurrentView {
    kairos_risk_contract::RiskCurrentView {
        actor_id: value.actor_id.clone(),
        generation: value.generation,
        event_sequence: value.event_sequence,
        policy_version: value.policy_version,
        limits: value
            .limits
            .iter()
            .map(|limit| kairos_risk_contract::LimitView {
                policy: policy(&limit.policy),
                used: amount(limit.used),
                reserved: amount(limit.reserved),
                available: amount(limit.available),
            })
            .collect(),
        reservations: value.reservations.iter().map(reservation).collect(),
        circuits: value.circuits.iter().map(circuit).collect(),
    }
}

pub(crate) fn event(value: &crate::RiskEvent) -> kairos_risk_contract::RiskEvent {
    match value {
        crate::RiskEvent::PolicyActivated {
            policy: value,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::PolicyActivated {
            policy: policy(value),
            event_sequence: *event_sequence,
        },
        crate::RiskEvent::ReservationChanged {
            reservation: value,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::ReservationChanged {
            reservation: reservation(value),
            event_sequence: *event_sequence,
        },
        crate::RiskEvent::DecisionEvaluated {
            decision: value,
            account_id,
            strategy_id,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::DecisionEvaluated {
            decision: decision(value, account_id, strategy_id, None),
            account_id: account_id.clone(),
            strategy_id: strategy_id.clone(),
            event_sequence: *event_sequence,
        },
        crate::RiskEvent::CircuitChanged {
            circuit: value,
            event_sequence,
        } => kairos_risk_contract::RiskEvent::CircuitChanged {
            circuit: circuit(value),
            event_sequence: *event_sequence,
        },
    }
}
