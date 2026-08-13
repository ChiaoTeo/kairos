//! Public Execution snapshot models.

use std::collections::BTreeMap;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Decimal(pub String);

impl Default for Decimal {
    fn default() -> Self {
        Self("0".into())
    }
}

impl Decimal {
    pub fn parts(&self) -> Result<(i64, u8), String> {
        let (negative, unsigned) = self
            .0
            .strip_prefix('-')
            .map_or((false, self.0.as_str()), |value| (true, value));
        let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
        if whole.is_empty()
            || fraction.len() > 18
            || !whole.bytes().all(|byte| byte.is_ascii_digit())
            || !fraction.bytes().all(|byte| byte.is_ascii_digit())
        {
            return Err("expected a decimal string with at most 18 fractional digits".into());
        }
        let magnitude = format!("{whole}{fraction}")
            .parse::<i128>()
            .map_err(|_| "decimal value is too large")?;
        let mantissa = i64::try_from(if negative { -magnitude } else { magnitude })
            .map_err(|_| "decimal value is too large")?;
        Ok((mantissa, fraction.len() as u8))
    }
}

impl serde::Serialize for Decimal {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        self.parts().map_err(serde::ser::Error::custom)?;
        serializer.serialize_str(&self.0)
    }
}

impl<'de> serde::Deserialize<'de> for Decimal {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = <String as serde::Deserialize>::deserialize(deserializer)?;
        let value = Self(value);
        value.parts().map_err(serde::de::Error::custom)?;
        Ok(value)
    }
}

#[cfg(test)]
mod decimal_tests {
    use super::Decimal;

    #[test]
    fn execution_decimal_contract_is_a_string() {
        let value = serde_json::from_str::<Decimal>("\"0.001250\"").unwrap();
        assert_eq!(value.parts().unwrap(), (1_250, 6));
        assert!(serde_json::from_str::<Decimal>(r#"{"mantissa":1250,"scale":6}"#).is_err());
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OrderSide {
    #[serde(alias = "buy")]
    Buy,
    #[serde(alias = "sell")]
    Sell,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum OrderType {
    Market,
    Limit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionOrderStatus {
    Pending,
    Submitting,
    Accepted,
    PartiallyFilled,
    Filled,
    CancelRequested,
    Canceled,
    Rejected,
    Expired,
    Unknown,
    Failed,
}

impl ExecutionOrderStatus {
    pub fn terminal(self) -> bool {
        matches!(
            self,
            Self::Filled | Self::Canceled | Self::Rejected | Self::Expired
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionOrder {
    pub order_id: String,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub intent_id: Option<String>,
    #[serde(default)]
    pub strategy_id: Option<String>,
    pub account_id: String,
    pub segment_key: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Decimal,
    pub limit_price: Option<Decimal>,
    pub remote_order_id: Option<String>,
    pub filled_quantity: Decimal,
    pub status: ExecutionOrderStatus,
    pub submitted_at_unix_nanos: u64,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionFill {
    pub fill_id: String,
    pub order_id: String,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub intent_id: Option<String>,
    pub instrument_id: String,
    #[serde(default)]
    pub execution_market_id: Option<String>,
    pub side: OrderSide,
    pub quantity: Decimal,
    pub price: Decimal,
    pub fee: Decimal,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum IntentStatus {
    Accepted,
    Planning,
    Planned,
    Executing,
    PartiallyFilled,
    CancelRequested,
    Satisfied,
    Rejected,
    Canceled,
    Expired,
    Failed,
    Compensating,
    ReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecuteStrategyIntent {
    pub intent_id: String,
    pub strategy_id: String,
    pub launch_id: String,
    pub instance_id: String,
    pub instrument_id: String,
    pub market_id: Option<String>,
    pub account_ids: Vec<String>,
    pub segment_key: String,
    pub target_quantity: Decimal,
    pub limit_price: Option<Decimal>,
    pub source_snapshot_id: Option<String>,
    pub source_event_sequence: Option<u64>,
    pub reason: String,
    #[serde(default)]
    pub intent_type: crate::plan::IntentType,
    #[serde(default)]
    pub completion_policy: crate::plan::CompletionPolicy,
    #[serde(default)]
    pub failure_policy: crate::plan::FailurePolicy,
    #[serde(default)]
    pub legs: Vec<crate::plan::ExecutionIntentLeg>,
    #[serde(default)]
    pub deadline_unix_nanos: Option<u64>,
    #[serde(default)]
    pub min_edge_bps: Option<u32>,
    #[serde(default)]
    pub max_slippage_bps: Option<u32>,
    #[serde(default)]
    pub estimated_fee_bps: Option<u32>,
    #[serde(default)]
    pub minimum_net_credit: Option<Decimal>,
    #[serde(default)]
    pub maximum_loss: Option<Decimal>,
    #[serde(default)]
    pub hedge_policy: Option<crate::plan::HedgePolicy>,
    #[serde(default)]
    pub order_options: crate::plan::ExecutionOrderOptions,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentState {
    pub intent: ExecuteStrategyIntent,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    #[serde(default)]
    pub plan: Option<crate::plan::ExecutionPlan>,
    #[serde(default)]
    pub completed_quantity: Decimal,
    pub updated_at_unix_nanos: u64,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
    #[serde(default)]
    pub quote_version: u64,
    #[serde(default)]
    pub last_quote_refresh_unix_nanos: Option<u64>,
    #[serde(default)]
    pub compensation_attempts: u32,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SnapshotWatermark {
    pub generation: u64,
    pub event_sequence: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DependencyWatermarks {
    #[serde(default)]
    pub account: BTreeMap<String, SnapshotWatermark>,
    pub market: Option<SnapshotWatermark>,
    pub reference: Option<SnapshotWatermark>,
    pub risk: Option<SnapshotWatermark>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct IntentEvent {
    pub intent_id: String,
    pub event_sequence: u64,
    pub status: IntentStatus,
    pub order_ids: Vec<String>,
    #[serde(default)]
    pub completed_quantity: Decimal,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
    #[serde(default)]
    pub dependency_watermarks: DependencyWatermarks,
}

/// State-only input accepted by mmap projection publishers. Event journals
/// and event cursors deliberately cannot be represented by this type.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionCurrentView {
    pub generation: u64,
    pub orders: Vec<ExecutionOrder>,
    pub intents: Vec<IntentState>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionEvent {
    pub order_id: String,
    #[serde(default)]
    pub intent_id: Option<String>,
    #[serde(default)]
    pub plan_id: Option<String>,
    #[serde(default)]
    pub leg_id: Option<String>,
    pub status: ExecutionOrderStatus,
    pub remote_order_id: Option<String>,
    pub occurred_at_unix_nanos: u64,
    pub reason: String,
    pub fill_id: Option<String>,
    pub filled_quantity: Option<Decimal>,
}
