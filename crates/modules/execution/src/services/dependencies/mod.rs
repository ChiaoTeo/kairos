//! Runtime intent planning and safety checks.
//!
//! Execution owns the plan and lifecycle, while this composition adapter
//! talks to the already-running Account/Risk/Market processes through their
//! Unix sockets.  No business state is cached here.

mod access;
mod order_admission;
mod planning;
mod projection;
mod workers;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_primitives::{InstrumentId, MarketId, Money, OrderId, Price, StrategyId, UnixNanos};
use kairos_reference_contract::ReferenceMarket;
use projection::*;
use rust_decimal::Decimal;
use serde_json::Value;

#[cfg(test)]
use crate::application::core::orders::admission::risk_amount;
use crate::application::core::orders::admission::{
    PlanningQuote, decimal_money, decimal_price, decimal_quantity, decimal_signed_quantity,
    ensure_available_capacity, money_from_decimal, quantity_from_decimal, validate_market_price,
    validate_pair_constraints, validate_quote_freshness, validate_quote_provisioning,
    validate_reference_rules,
};
use crate::application::{
    DependencyWatermarks, ExecuteStrategyIntent, QuoteObservation, RiskAuthorizationContext,
    SnapshotWatermark, SubmitOrder,
};
use crate::domain::{CommitmentBasis, CommitmentResource, OrderCommitment, OrderSide, OrderType};
use crate::services::risk::SocketExecutionRiskReservations;

/// Composition-owned projection of the Market quote view. This is an adapter
/// record, not a public backtest/replay model.
type MarketQuote = PlanningQuote;

use access::ExecutionDependencyAccess;
use order_admission::OrderAdmissionContext;
use planning::IntentPlanningContext;
pub use workers::{QueuedExecutionIntentPlanner, QueuedExecutionOrderAdmission};

pub(crate) enum ExecutionOrderAdmissionService {
    Live(QueuedExecutionOrderAdmission),
    Simulated,
}

impl ExecutionOrderAdmissionService {
    pub(crate) fn live(admission: QueuedExecutionOrderAdmission) -> Self {
        Self::Live(admission)
    }

    pub(crate) fn simulated() -> Self {
        Self::Simulated
    }

    pub(crate) fn dependency_watermarks(&self) -> DependencyWatermarks {
        match self {
            Self::Live(admission) => admission.dependency_watermarks(),
            Self::Simulated => DependencyWatermarks::default(),
        }
    }

    pub(crate) fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
        now: u64,
    ) -> Result<OrderCommitment, String> {
        match self {
            Self::Live(admission) => admission.validate_order(request, active_commitments),
            Self::Simulated => {
                crate::application::core::orders::admission::simulation_commitment(request, now)
            },
        }
    }

    pub(crate) fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
        route: &crate::application::ExecutionRouteCandidate,
    ) -> Result<RiskAuthorizationContext, String> {
        match self {
            Self::Live(admission) => admission.risk_authorization_context(request, route),
            Self::Simulated => Ok(RiskAuthorizationContext {
                initial_margin_rate_bps: route.initial_margin_rate_bps,
                margin_rule_id: route.margin_rule_id.clone(),
                ..RiskAuthorizationContext::default()
            }),
        }
    }
}

pub struct SocketExecutionIntentPlanner {
    context: IntentPlanningContext,
}

impl SocketExecutionIntentPlanner {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        IntentPlanningContext::from_manifest(path).map(|context| Self { context })
    }

    pub fn without_market_snapshot(mut self) -> Self {
        self.context = self.context.without_market_snapshot();
        self
    }
}

impl SocketExecutionIntentPlanner {
    pub(crate) fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        self.context.advance_time(event_time_unix_nanos)
    }

    pub(crate) fn plan_intent(
        &mut self,
        intent: &ExecuteStrategyIntent,
    ) -> Result<Vec<SubmitOrder>, String> {
        self.context.plan_intent(intent)
    }

    pub(crate) fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        self.context.latest_quote(instrument_id, market_id)
    }

    pub(crate) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.context.dependency_watermarks()
    }
}

pub struct SocketExecutionOrderAdmission {
    context: OrderAdmissionContext,
}

impl SocketExecutionOrderAdmission {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        OrderAdmissionContext::from_manifest(path).map(|context| Self { context })
    }

    pub fn without_market_snapshot(mut self) -> Self {
        self.context = self.context.without_market_snapshot();
        self
    }

    pub fn with_backtest_reservation_window(mut self) -> Self {
        self.context = self.context.with_backtest_reservation_window();
        self
    }

    pub fn with_backtest_reference_without_projection(mut self, enabled: bool) -> Self {
        self.context = self
            .context
            .with_backtest_reference_without_projection(enabled);
        self
    }

    pub fn with_backtest_balance_without_projection(mut self, enabled: bool) -> Self {
        self.context = self
            .context
            .with_backtest_balance_without_projection(enabled);
        self
    }

    pub fn risk_reservations_adapter(&self) -> Result<SocketExecutionRiskReservations, String> {
        self.context.risk_reservations_adapter()
    }
}

impl SocketExecutionOrderAdmission {
    pub(crate) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.context.dependency_watermarks()
    }

    pub(crate) fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
    ) -> Result<OrderCommitment, String> {
        self.context.validate_order(request, active_commitments)
    }

    pub(crate) fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
        route: &crate::application::ExecutionRouteCandidate,
    ) -> Result<RiskAuthorizationContext, String> {
        self.context.risk_authorization_context(request, route)
    }
}

fn find_available(response: &[ProjectedBalance], asset: &str) -> Result<Option<Decimal>, String> {
    response
        .iter()
        .find(|balance| balance.asset_code.eq_ignore_ascii_case(asset))
        .and_then(|balance| balance.available.as_ref())
        .map(|value| {
            Decimal::try_new(value.mantissa(), u32::from(value.scale()))
                .map_err(|_| "available balance cannot be represented as a decimal".to_string())
        })
        .transpose()
}

fn find_position(
    response: &[ProjectedPosition],
    instrument: &str,
) -> Result<Option<Decimal>, String> {
    response
        .iter()
        .find(|position| position.instrument_id.eq_ignore_ascii_case(instrument))
        .map(|position| {
            Decimal::try_new(
                position.quantity.mantissa(),
                u32::from(position.quantity.scale()),
            )
            .map_err(|_| "position quantity cannot be represented as a decimal".to_string())
        })
        .transpose()
}

#[cfg(test)]
mod tests {
    use kairos_primitives::{Price, Quantity};

    use super::{decimal_price, decimal_quantity, risk_amount};

    #[test]
    fn risk_notional_preserves_quantity_and_price_scales() {
        let quantity = decimal_quantity(Quantity::new(2, 0).unwrap()).unwrap();
        let price = decimal_price(Price::new(1_005, 1).unwrap()).unwrap();
        let amount = risk_amount(quantity * price).unwrap();

        assert_eq!((amount.mantissa(), amount.scale()), (201, 0));
    }
}
