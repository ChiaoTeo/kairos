//! Execution fill facts.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionFill {
    pub fill_id: FillId,
    pub order_id: OrderId,
    #[serde(default)]
    pub plan_id: Option<PlanId>,
    #[serde(default)]
    pub leg_id: Option<LegId>,
    pub intent_id: Option<IntentId>,
    pub instrument_id: InstrumentId,
    /// The canonical Market where this fill actually occurred. For smart/SOR
    /// routes this may differ between fills of one order and is intentionally
    /// distinct from the order's requested/pricing market.
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    #[serde(default)]
    pub reported_provider_id: Option<String>,
    #[serde(default)]
    pub provider_product: Option<kairos_primitives::integration::ProviderProductCode>,
    #[serde(default)]
    pub provider_symbol: Option<kairos_primitives::integration::ProviderSymbol>,
    #[serde(default)]
    pub remote_order_id: Option<RemoteOrderId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    /// Currency in which the provider charged the fee.
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: UnixNanos,
}
