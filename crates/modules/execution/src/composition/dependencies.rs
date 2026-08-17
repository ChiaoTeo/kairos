//! Runtime intent planning and safety checks.
//!
//! Execution owns the plan and lifecycle, while this composition adapter
//! talks to the already-running Account/Risk/Market processes through their
//! Unix sockets.  No business state is cached here.

use super::admission_rules::*;
use super::dependency_projection::*;
use super::risk_reservations::SocketExecutionRiskReservations;
use crate::application::market_input::Quote as MarketQuote;
use crate::application::{
    DependencyWatermarks, ExecuteStrategyIntent, ExecutionIntentPlanner, ExecutionOrderAdmission,
    QuoteObservation, RiskAuthorizationContext, SnapshotWatermark, SubmitOrder,
};
use crate::domain::{CommitmentBasis, CommitmentResource, OrderCommitment, OrderSide, OrderType};
use kairos_primitives::{InstrumentId, MarketId, Money, OrderId, Price, StrategyId, UnixNanos};
use rust_decimal::Decimal;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_reference_contract::ReferenceMarket;

mod access;
mod intent_planning;
mod order_admission;
use access::ExecutionDependencyAccess;
use intent_planning::IntentPlanningContext;
use order_admission::OrderAdmissionContext;

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

impl ExecutionIntentPlanner for SocketExecutionIntentPlanner {
    fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        self.context.advance_time(event_time_unix_nanos)
    }

    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        self.context.plan_intent(intent)
    }

    fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        self.context.latest_quote(instrument_id, market_id)
    }

    fn dependency_watermarks(&self) -> DependencyWatermarks {
        ExecutionIntentPlanner::dependency_watermarks(&self.context)
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

impl ExecutionOrderAdmission for SocketExecutionOrderAdmission {
    fn dependency_watermarks(&self) -> DependencyWatermarks {
        ExecutionOrderAdmission::dependency_watermarks(&self.context)
    }

    fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
    ) -> Result<OrderCommitment, String> {
        self.context.validate_order(request, active_commitments)
    }

    fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
    ) -> Result<RiskAuthorizationContext, String> {
        self.context.risk_authorization_context(request)
    }
}

fn find_available(response: &[ProjectedBalance], asset: &str) -> Result<Option<Decimal>, String> {
    response
        .iter()
        .find(|balance| balance.asset_code.eq_ignore_ascii_case(asset))
        .and_then(|balance| balance.available.as_ref())
        .map(|value| {
            Decimal::try_new(value.mantissa, u32::from(value.scale))
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
                position.quantity.mantissa,
                u32::from(position.quantity.scale),
            )
            .map_err(|_| "position quantity cannot be represented as a decimal".to_string())
        })
        .transpose()
}

#[cfg(test)]
mod tests {
    use super::{decimal_price, decimal_quantity, risk_amount, DependencyProjectionRuntime};
    use kairos_primitives::{Price, Quantity};
    use std::collections::BTreeMap;
    use std::time::{Duration, Instant};

    #[test]
    fn reference_mmap_builds_execution_watermark() {
        let root = tempfile::tempdir().unwrap();
        let actor_id = "reference-actor";
        let mut publisher = kairos_reference_contract::MmapReferenceLatestPublisher::create(
            root.path(),
            actor_id,
            1024 * 1024,
        )
        .unwrap();
        publisher
            .publish(&kairos_reference_contract::ReferenceLatestSnapshot {
                actor_id: actor_id.into(),
                workspace_id: "workspace:test".into(),
                generation: 7,
                event_sequence: 11,
                ..Default::default()
            })
            .unwrap();

        let projection = DependencyProjectionRuntime::start(
            &BTreeMap::new(),
            &BTreeMap::new(),
            None,
            Some(root.path().to_path_buf()),
            Some(actor_id.into()),
            None,
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if projection.watermarks().reference.is_some() {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Execution did not project the Reference snapshot"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        let reference = projection.watermarks().reference.unwrap();
        assert_eq!(reference.generation.get(), 7);
        assert_eq!(reference.event_sequence.get(), 11);
    }

    #[test]
    fn risk_notional_preserves_quantity_and_price_scales() {
        let quantity = decimal_quantity(Quantity::new(2, 0).unwrap()).unwrap();
        let price = decimal_price(Price::new(1_005, 1).unwrap()).unwrap();
        let amount = risk_amount(quantity * price).unwrap();

        assert_eq!((amount.mantissa, amount.scale), (201, 0));
    }
}
