#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandEnvelope {
    pub command_type: String,
    pub request_id: String,
    pub payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueryEnvelope {
    pub query_type: String,
    pub request_id: String,
    pub payload: Vec<u8>,
}

/// Stable JSON command envelope used by strategy and system callers.  The
/// transport may change, but these fields remain the idempotent application
/// boundary of Execution.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentCommandEnvelope<T> {
    pub schema_version: u16,
    pub command_id: String,
    pub idempotency_key: String,
    pub operation: String,
    pub strategy_id: String,
    pub instance_id: String,
    #[serde(default)]
    pub launch_id: String,
    pub payload: T,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SubmitIntentPayload {
    pub intent: crate::model::ExecuteStrategyIntent,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "intent_type", content = "intent")]
pub enum ExecutionIntentCommand {
    SingleOrder(Box<crate::model::ExecuteStrategyIntent>),
    PairArbitrage(Box<crate::plan::PairArbitrageIntent>),
    PortfolioRebalance(Box<crate::plan::PortfolioRebalanceIntent>),
    QuoteProvisioning(Box<crate::model::ExecuteStrategyIntent>),
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct CancelIntentCommand {
    pub intent_id: String,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExpireIntentCommand {
    pub intent_id: String,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RefreshQuoteCommand {
    pub intent_id: String,
    pub bid_price_mantissa: i64,
    pub bid_price_scale: u8,
    pub ask_price_mantissa: i64,
    pub ask_price_scale: u8,
    pub quote_observed_at_unix_nanos: u64,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentQuery {
    pub intent_id: Option<String>,
    pub strategy_id: Option<String>,
    pub status: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentEventQuery {
    pub intent_id: Option<String>,
    #[serde(default)]
    pub after_sequence: u64,
    pub limit: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct HedgeRequirementQuery {
    pub intent_id: String,
}
