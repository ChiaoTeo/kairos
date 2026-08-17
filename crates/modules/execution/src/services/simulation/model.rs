use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulationConfig {
    #[serde(default)]
    pub fee_bps: Rate,
    /// Currency used to pay simulated fees. It is intentionally explicit;
    /// the simulator must not infer it from the settlement asset.
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    #[serde(default)]
    pub slippage_bps: Rate,
    #[serde(default = "default_true")]
    pub enforce_quote_quantity: bool,
}

impl Default for SimulationConfig {
    fn default() -> Self {
        Self {
            fee_bps: Rate::ZERO,
            fee_currency: None,
            slippage_bps: Rate::ZERO,
            enforce_quote_quantity: true,
        }
    }
}

impl SimulationConfig {
    pub(super) fn validate(&self) -> Result<(), String> {
        if self.fee_bps < Rate::ZERO {
            return Err("simulation fee_bps must be non-negative".into());
        }
        if self.fee_bps > Rate::ZERO && self.fee_currency.is_none() {
            return Err("simulation fee_currency is required when fee_bps is non-zero".into());
        }
        if self.slippage_bps < Rate::ZERO {
            return Err("simulation slippage_bps must be non-negative".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulationOrderRequest {
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    #[serde(default)]
    pub limit_price: Option<Price>,
    pub submitted_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SimulationOrderStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationOrder {
    pub request: SimulationOrderRequest,
    pub status: SimulationOrderStatus,
    pub filled_quantity: Quantity,
    pub remaining_quantity: Quantity,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationFill {
    pub fill_id: FillId,
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SimulationResult {
    pub orders: Vec<SimulationOrder>,
    pub fills: Vec<SimulationFill>,
}

#[derive(Clone, Debug)]
pub(super) struct WorkingOrder {
    pub(super) order: SimulationOrder,
    pub(super) quantity: Decimal,
    pub(super) filled_quantity: Decimal,
    pub(super) limit_price: Option<Decimal>,
}

pub struct ExecutionSimulator {
    pub(super) config: SimulationConfig,
    pub(super) orders: BTreeMap<String, WorkingOrder>,
    pub(super) fills: Vec<SimulationFill>,
    pub(super) next_fill_id: u64,
    pub(super) last_market_event_time: Option<UnixNanos>,
}

fn default_true() -> bool {
    true
}
