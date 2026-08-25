//! Public Execution commands.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SubmitOrder {
    pub order_id: OrderId,
    pub intent_id: Option<IntentId>,
    #[serde(default)]
    pub strategy_id: Option<kairos_primitives::runtime::StrategyId>,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    /// Explicit Execution route selected by planning or the caller.
    /// Execution never derives provider identity from a MarketId or symbol.
    #[serde(default)]
    pub execution_route_id: Option<ExecutionRouteId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub options: ExecutionOrderOptions,
    /// Business time of the causal event. `None` means use processing time.
    pub submitted_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionOrderOptions {
    pub time_in_force: Option<String>,
    pub reduce_only: Option<bool>,
    pub post_only: Option<bool>,
    pub position_side: Option<String>,
    pub quote_asset: Option<String>,
    pub wallet_type: Option<String>,
    pub trading_session: Option<String>,
    pub tokenize: Option<bool>,
    #[serde(default)]
    pub split: Option<SplitOrderPolicy>,
    #[serde(default)]
    pub maker: Option<MakerExecutionPolicy>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelOrder {
    pub order_id: OrderId,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CancelIntent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExpireIntent {
    pub intent_id: IntentId,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReplaceOrder {
    pub order_id: OrderId,
    pub replacement: SubmitOrder,
}

/// Replace the two live legs of a QuoteProvisioning intent as one
/// Execution-owned operation.  Strategies provide a fresh quote; Execution
/// owns the cancel/re-submit sequence and keeps the old orders in the same
/// plan for audit and fill aggregation.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RefreshQuoteIntent {
    pub intent_id: IntentId,
    pub bid_price: Price,
    pub ask_price: Price,
    pub quote_observed_at: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct QuoteObservation {
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub bid_price: Option<Price>,
    pub ask_price: Option<Price>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFillReport {
    pub fill_id: FillId,
    pub order_id: OrderId,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    #[serde(default)]
    pub reported_broker_id: Option<kairos_primitives::account::BrokerId>,
    #[serde(default)]
    pub execution_channel: Option<kairos_primitives::execution::ExecutionChannelCode>,
    #[serde(default, alias = "provider_symbol")]
    pub order_entry_symbol: Option<kairos_primitives::execution::OrderEntrySymbol>,
    #[serde(default)]
    pub remote_order_id: Option<RemoteOrderId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecuteStrategyIntent {
    pub intent_id: IntentId,
    /// Opaque identity of the Strategy-owned decision that caused this Intent.
    ///
    /// This remains optional while legacy and non-Strategy control callers are
    /// migrated. Execution preserves the value but never interprets it.
    #[serde(default)]
    pub strategy_decision_id: Option<String>,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_route_id: Option<ExecutionRouteId>,
    pub account_ids: Vec<AccountId>,
    pub segment_key: SegmentKey,
    pub target_quantity: Quantity,
    pub limit_price: Option<Price>,
    pub source_snapshot_id: Option<String>,
    pub source_event_sequence: Option<Sequence>,
    pub source_event_time_unix_nanos: Option<UnixNanos>,
    pub reason: String,
    pub intent_type: IntentType,
    pub algorithm: ExecutionAlgorithmPolicy,
    pub completion_policy: CompletionPolicy,
    pub failure_policy: FailurePolicy,
    pub legs: Vec<IntentLegRequest>,
    pub deadline_unix_nanos: Option<UnixNanos>,
    pub min_edge_bps: Option<u32>,
    pub max_slippage_bps: Option<u32>,
    /// Estimated round-trip fees for a multi-leg execution.  This is an
    /// advisory input used by admission to reject an edge that is only
    /// positive before fees.
    pub estimated_fee_bps: Option<u32>,
    #[serde(default)]
    pub minimum_net_credit: Option<Money>,
    #[serde(default)]
    pub maximum_loss: Option<Money>,
    pub order_options: ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentAdmissionEvidence {
    pub source: String,
    pub decision_id: String,
    pub outcome: String,
    pub original_intent: ExecuteStrategyIntent,
    pub effective_intent: ExecuteStrategyIntent,
    pub original_hash: String,
    pub effective_hash: String,
}

#[cfg(test)]
impl ExecuteStrategyIntent {
    pub(crate) fn test_fixture() -> Self {
        Self {
            intent_id: IntentId::new("intent:default").expect("valid fixture intent ID"),
            strategy_decision_id: None,
            strategy_id: String::new(),
            launch_id: String::new(),
            instance_id: String::new(),
            instrument_id: InstrumentId::new("instrument:default")
                .expect("valid fixture instrument ID"),
            market_id: None,
            execution_route_id: None,
            account_ids: Vec::new(),
            segment_key: SegmentKey::new("segment:default").expect("valid fixture segment key"),
            target_quantity: Quantity::new(0, 0).expect("valid fixture quantity"),
            limit_price: None,
            source_snapshot_id: None,
            source_event_sequence: None,
            source_event_time_unix_nanos: None,
            reason: String::new(),
            intent_type: IntentType::default(),
            algorithm: ExecutionAlgorithmPolicy::Immediate,
            completion_policy: CompletionPolicy::default(),
            failure_policy: FailurePolicy::default(),
            legs: Vec::new(),
            deadline_unix_nanos: None,
            min_edge_bps: None,
            max_slippage_bps: None,
            estimated_fee_bps: None,
            minimum_net_credit: None,
            maximum_loss: None,
            order_options: ExecutionOrderOptions::default(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IntentLegRequest {
    pub leg_id: LegId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub execution_route_id: Option<ExecutionRouteId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub limit_price: Option<Price>,
    pub target_position: bool,
    pub options: ExecutionOrderOptions,
}
