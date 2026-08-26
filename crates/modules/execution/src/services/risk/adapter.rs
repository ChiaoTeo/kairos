//! Risk reservation command adapter and typed indexed-view reconciliation.

use kairos_primitives::decimal::Money;
use kairos_primitives::risk::ReservationId;
use kairos_primitives::runtime::{
    ActorId, IdempotencyKey, InstanceIdentity, RequestId, StrategyId,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_protocol::generated::kairos::risk::v_2::ReservationStatus as RiskViewReservationStatus;
use kairos_risk_contract::{
    Amount, AuthorizeRequest, ConsumeReservationRequest, ReleaseReservationRequest,
    ResizeReservationRequest, RiskClient, RiskContext, RiskControlRpcClient, RiskDecision,
    TradeRiskProposal,
};
use rust_decimal::Decimal;

use crate::application::{
    ExecutionFundingRequirement, RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult,
    SubmitOrder,
};
use crate::domain::{RiskReservationEvidence, RiskReservationSagaStatus};

pub struct SocketExecutionRiskReservations {
    risk: RiskClient,
    risk_actor_id: String,
    identity: InstanceIdentity,
    reservation_ttl_nanos: u64,
    skip_authorization: bool,
}

impl SocketExecutionRiskReservations {
    pub(crate) fn new(
        risk: RiskClient,
        risk_actor_id: String,
        identity: InstanceIdentity,
        reservation_ttl_nanos: u64,
        skip_authorization: bool,
    ) -> Self {
        Self {
            risk,
            risk_actor_id,
            identity,
            reservation_ttl_nanos,
            skip_authorization,
        }
    }

    fn risk_runtime() -> RiskCommandResult<tokio::runtime::Runtime> {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))
    }

    fn health(&mut self) -> RiskCommandResult<kairos_risk_contract::Health> {
        Self::risk_runtime()?
            .block_on(RiskControlRpcClient::health(&self.risk.control()))
            .map_err(map_rpc_error)
    }

    fn authorize_and_reserve(
        &mut self,
        request: AuthorizeRequest,
    ) -> RiskCommandResult<RiskDecision> {
        Self::risk_runtime()?
            .block_on(RiskControlRpcClient::authorize_and_reserve(
                &self.risk.control(),
                request,
            ))
            .map_err(map_rpc_error)
    }

    fn resize_reservation(&mut self, request: ResizeReservationRequest) -> RiskCommandResult<()> {
        Self::risk_runtime()?
            .block_on(RiskControlRpcClient::resize_reservation(
                &self.risk.control(),
                request,
            ))
            .map_err(map_rpc_error)
            .map(|_| ())
    }

    fn release_reservation(&mut self, request: ReleaseReservationRequest) -> RiskCommandResult<()> {
        Self::risk_runtime()?
            .block_on(RiskControlRpcClient::release_reservation(
                &self.risk.control(),
                request,
            ))
            .map_err(map_rpc_error)
            .map(|_| ())
    }

    fn consume_reservation(&mut self, request: ConsumeReservationRequest) -> RiskCommandResult<()> {
        Self::risk_runtime()?
            .block_on(RiskControlRpcClient::consume_reservation(
                &self.risk.control(),
                request,
            ))
            .map_err(map_rpc_error)
            .map(|_| ())
    }
}

impl SocketExecutionRiskReservations {
    pub(super) fn authorize(
        &mut self,
        request: &SubmitOrder,
        context: &RiskAuthorizationContext,
    ) -> RiskCommandResult<RiskReservationEvidence> {
        let reservation_id = ReservationId::new(format!("execution:{}", request.order_id))
            .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?;
        let amount = notional_amount(request).map_err(RiskCommandFailure::NotSent)?;
        let now = request
            .submitted_at_unix_nanos
            .unwrap_or_else(|| UnixNanos::new(now_unix_nanos()));
        if self.skip_authorization {
            return evidence(
                request,
                reservation_id,
                amount,
                Generation::default(),
                Sequence::default(),
                Generation::default(),
                now + self.reservation_ttl_nanos,
                now,
            )
            .map_err(RiskCommandFailure::NotSent);
        }

        let health = self.health()?;
        if health.status != "ready" {
            return Err(RiskCommandFailure::Rejected("risk is not ready".into()));
        }
        let zero = Amount::default();
        let available_margin = context
            .available_margin
            .map(|value| {
                Amount::new(value.mantissa(), value.scale())
                    .expect("Money satisfies Risk contract decimal bounds")
            })
            .unwrap_or_default();
        let reservation_ttl_nanos = self.reservation_ttl_nanos;
        let authorization = AuthorizeRequest {
            request_id: RequestId::new(request.order_id.to_string())
                .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
            idempotency_key: IdempotencyKey::new(reservation_id.to_string())
                .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
            reservation_id: reservation_id.clone(),
            account_id: request.account_id.clone(),
            strategy_id: risk_strategy_id(request.strategy_id.as_ref())
                .map_err(RiskCommandFailure::NotSent)?,
            instrument_id: request.instrument_id.clone(),
            exchange_id: context.exchange_id.clone().ok_or_else(|| {
                RiskCommandFailure::NotSent(
                    "Reference market is missing its exchange identity".into(),
                )
            })?,
            proposal: TradeRiskProposal {
                notional: amount,
                initial_margin_rate_bps: u64::from(context.initial_margin_rate_bps.ok_or_else(
                    || {
                        RiskCommandFailure::NotSent(
                            "execution route is missing an initial margin rule".into(),
                        )
                    },
                )?)
                .into(),
                account_segment: context.funding_segment.clone().ok_or_else(|| {
                    RiskCommandFailure::NotSent(
                        "execution route is missing its funding segment".into(),
                    )
                })?,
                collateral_asset: context.collateral_asset.clone().ok_or_else(|| {
                    RiskCommandFailure::NotSent(
                        "execution route is missing its collateral asset".into(),
                    )
                })?,
                reduce_only: request.options.reduce_only.unwrap_or(false),
                margin_rule_id: kairos_primitives::risk::MarginRuleCode::new(
                    context.margin_rule_id.clone().ok_or_else(|| {
                        RiskCommandFailure::NotSent(
                            "execution route is missing a margin rule identity".into(),
                        )
                    })?,
                )
                .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
            },
            at_unix_nanos: now,
            reservation_ttl_nanos: reservation_ttl_nanos.into(),
            dependency_generation: health.generation,
            dependency_event_sequence: health.event_sequence,
            context: Some(RiskContext {
                account_snapshot_watermark: UnixNanos::new(context.account.generation.get()),
                market_freshness_watermark: context
                    .market
                    .as_ref()
                    .map(|value| value.generation.get().max(value.event_sequence.get()))
                    .map(UnixNanos::new)
                    .unwrap_or_default(),
                portfolio_version: context.account.generation,
                current_exposure: zero,
                current_margin: zero,
                available_margin,
                current_pnl: zero,
                current_drawdown: zero,
                market_is_fresh: context.market_is_fresh,
                leverage_bps: 0.into(),
                price_deviation_bps: 0.into(),
                stress_loss: zero,
            }),
        };
        let decision: RiskDecision = self.authorize_and_reserve(authorization)?;
        if !decision.allowed {
            if let Some(requirement) = decision
                .funding_requirement
                .as_ref()
                .filter(|requirement| requirement.shortfall.mantissa() > 0)
            {
                return Err(RiskCommandFailure::DeferredInsufficientFunding {
                    requirement: ExecutionFundingRequirement {
                        required_margin: Money::new(
                            requirement.required_margin.mantissa(),
                            requirement.required_margin.scale(),
                        )
                        .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
                        available_margin: Money::new(
                            requirement.available_margin.mantissa(),
                            requirement.available_margin.scale(),
                        )
                        .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
                        shortfall: Money::new(
                            requirement.shortfall.mantissa(),
                            requirement.shortfall.scale(),
                        )
                        .map_err(|error| RiskCommandFailure::NotSent(error.to_string()))?,
                        margin_rule_id: requirement.margin_rule_id.to_string(),
                        risk_decision_id: decision.decision_id.clone(),
                        risk_policy_version: decision.policy_version,
                        account_snapshot_watermark: decision
                            .context
                            .as_ref()
                            .map(|context| context.account_snapshot_watermark)
                            .unwrap_or_default(),
                        broker: context.funding_broker.clone().ok_or_else(|| {
                            RiskCommandFailure::NotSent(
                                "execution route is missing its funding broker".into(),
                            )
                        })?,
                        segment: requirement.account_segment.clone(),
                        collateral_asset: requirement.collateral_asset.clone(),
                    },
                });
            }
            return Err(RiskCommandFailure::Rejected(
                if decision.violations.is_empty() {
                    "risk authorization rejected order".into()
                } else {
                    decision.violations.join("; ")
                },
            ));
        }
        let reservation = decision.reservation.ok_or_else(|| {
            RiskCommandFailure::Indeterminate(
                "risk authorization did not return its reservation".into(),
            )
        })?;
        evidence(
            request,
            reservation_id,
            amount,
            health.generation,
            health.event_sequence,
            reservation.policy_version,
            reservation.expires_at_unix_nanos,
            reservation.updated_at_unix_nanos,
        )
        .map_err(RiskCommandFailure::Indeterminate)
    }

    pub(super) fn reconcile(
        &mut self,
        evidence: &RiskReservationEvidence,
    ) -> Result<Option<RiskReservationEvidence>, String> {
        read_reservation(&self.risk, &self.risk_actor_id, &self.identity, evidence)
    }

    pub(super) fn resize(
        &mut self,
        evidence: &RiskReservationEvidence,
        amount: Money,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        if self.skip_authorization {
            return Ok(());
        }
        self.resize_reservation(ResizeReservationRequest {
            reservation_id: evidence.reservation_id.clone(),
            amount: Amount::new(amount.mantissa(), amount.scale())
                .expect("Money satisfies Risk contract decimal bounds"),
            at_unix_nanos: at,
        })
    }

    pub(super) fn release(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        if self.skip_authorization {
            return Ok(());
        }
        self.release_reservation(ReleaseReservationRequest {
            reservation_id: evidence.reservation_id.clone(),
            at_unix_nanos: at,
        })
    }

    pub(super) fn consume(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        if self.skip_authorization {
            return Ok(());
        }
        self.consume_reservation(ConsumeReservationRequest {
            reservation_id: evidence.reservation_id.clone(),
            at_unix_nanos: at,
        })
    }
}

fn map_rpc_error(error: jsonrpsee::core::client::Error) -> RiskCommandFailure {
    let message = error.to_string();
    if message.contains("No such file")
        || message.contains("Connection refused")
        || message.contains("connection refused")
        || message.contains("not found")
    {
        RiskCommandFailure::NotSent(message)
    } else {
        RiskCommandFailure::Indeterminate(message)
    }
}

fn notional_amount(request: &SubmitOrder) -> Result<Amount, String> {
    let quantity = Decimal::try_new(
        request.quantity.mantissa(),
        u32::from(request.quantity.scale()),
    )
    .map_err(|_| "quantity cannot be represented as a decimal".to_string())?;
    let price = request
        .limit_price
        .map(|value| Decimal::try_new(value.mantissa(), u32::from(value.scale())))
        .transpose()
        .map_err(|_| "price cannot be represented as a decimal".to_string())?
        .unwrap_or(Decimal::ONE);
    let value = quantity
        .checked_mul(price)
        .ok_or_else(|| "risk notional overflow".to_string())?
        .normalize();
    if value.scale() > u32::from(kairos_primitives::decimal::MAX_DECIMAL_SCALE) {
        return Err("risk amount exceeds 18 fractional digits".into());
    }
    Amount::new(
        i64::try_from(value.mantissa())
            .map_err(|_| "risk amount exceeds Decimal64 range".to_string())?,
        value.scale() as u8,
    )
    .map_err(|error| error.to_string())
}

fn risk_strategy_id(strategy_id: Option<&StrategyId>) -> Result<StrategyId, String> {
    strategy_id
        .cloned()
        .ok_or_else(|| "risk authorization requires SubmitOrder.strategy_id".into())
}

#[allow(clippy::too_many_arguments)]
fn evidence(
    request: &SubmitOrder,
    reservation_id: ReservationId,
    amount: Amount,
    risk_generation: Generation,
    risk_event_sequence: Sequence,
    policy_version: Generation,
    expires_at_unix_nanos: UnixNanos,
    updated_at_unix_nanos: UnixNanos,
) -> Result<RiskReservationEvidence, String> {
    Ok(RiskReservationEvidence {
        order_id: request.order_id.clone(),
        idempotency_key: IdempotencyKey::new(reservation_id.to_string())
            .map_err(|error| error.to_string())?,
        reservation_id,
        account_id: request.account_id.clone(),
        amount: Money::new(amount.mantissa(), amount.scale()).map_err(|error| error.to_string())?,
        status: RiskReservationSagaStatus::Active,
        risk_generation,
        risk_event_sequence,
        policy_version,
        expires_at_unix_nanos,
        updated_at_unix_nanos,
        funding_requirement: None,
    })
}

fn read_reservation(
    client: &RiskClient,
    actor_id: &str,
    identity: &InstanceIdentity,
    evidence: &RiskReservationEvidence,
) -> Result<Option<RiskReservationEvidence>, String> {
    let actor_id = ActorId::new(actor_id).map_err(|error| error.to_string())?;
    let snapshot = client
        .indexed_current(identity, actor_id)
        .and_then(|view| view.snapshot())
        .map_err(|error| format!("read Risk indexed view: {error}"))?;
    let values = snapshot.reservations();
    let mut matched = None;
    for value in &values {
        let current = value
            .reservation()
            .map_err(|error| format!("decode Risk indexed reservation: {error}"))?;
        let reservation = current.reservation();
        if evidence.reservation_id == reservation.reservation_id()
            || evidence.idempotency_key.as_str() == reservation.idempotency_key()
        {
            matched = Some(reservation);
            break;
        }
    }
    let Some(reservation) = matched else {
        return Ok(None);
    };
    if reservation.account_id() != Some(evidence.account_id.as_str()) {
        return Err("Risk reservation account identity mismatch".into());
    }
    let status = match reservation.status() {
        RiskViewReservationStatus::RESERVED => RiskReservationSagaStatus::Active,
        RiskViewReservationStatus::CONSUMED => RiskReservationSagaStatus::Consumed,
        RiskViewReservationStatus::RELEASED => RiskReservationSagaStatus::Released,
        RiskViewReservationStatus::EXPIRED => RiskReservationSagaStatus::Expired,
        _ => return Err("Risk reservation has an unspecified lifecycle".into()),
    };
    let amount = evidence.amount;
    Ok(Some(RiskReservationEvidence {
        order_id: evidence.order_id.clone(),
        reservation_id: ReservationId::new(reservation.reservation_id())
            .map_err(|error| error.to_string())?,
        idempotency_key: IdempotencyKey::new(reservation.idempotency_key())
            .map_err(|error| error.to_string())?,
        account_id: evidence.account_id.clone(),
        amount,
        status,
        risk_generation: snapshot.metadata().applied_event_sequence.into(),
        risk_event_sequence: snapshot.metadata().applied_event_sequence.into(),
        policy_version: reservation.policy_version().into(),
        expires_at_unix_nanos: reservation.expires_at_unix_nanos().into(),
        updated_at_unix_nanos: reservation.updated_at_unix_nanos().into(),
        funding_requirement: evidence.funding_requirement.clone(),
    }))
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use kairos_primitives::runtime::StrategyId;

    use super::risk_strategy_id;

    #[test]
    fn risk_identity_uses_strategy_id_and_has_no_intent_fallback() {
        let strategy_id = StrategyId::new("strategy-alpha").unwrap();

        assert_eq!(
            risk_strategy_id(Some(&strategy_id)).unwrap().as_str(),
            "strategy-alpha"
        );
        assert_eq!(
            risk_strategy_id(None).unwrap_err(),
            "risk authorization requires SubmitOrder.strategy_id"
        );
    }
}
