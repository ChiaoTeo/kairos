use kairos_primitives::account::SegmentKey;
use kairos_primitives::decimal::{Price, Quantity, SignedQuantity};
use kairos_primitives::execution::{FillId, OrderId, OrderSide};
use kairos_primitives::reference::{Currency, InstrumentId};
use kairos_primitives::runtime::IdempotencyKey;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountHealthStatus {
    Ready,
    Degraded,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct Health {
    pub status: AccountHealthStatus,
    #[serde(default)]
    pub lease_valid: Option<bool>,
    pub generation: Generation,
    pub event_sequence: Sequence,
}

pub type DecimalValue = kairos_primitives::decimal::DecimalParts;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedSettlement {
    pub fill_id: FillId,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_id: Option<OrderId>,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quantity: Quantity,
    pub price: Price,
    pub side: OrderSide,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub settlement_asset: Option<Currency>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub settlement_delta: Option<SignedQuantity>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_asset: Option<Currency>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fee_amount: Option<SignedQuantity>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SimulatedCapitalMutationKind {
    DebitLiquid,
    CreditLiquid,
    SubscribeEarn,
    RedeemEarn,
}

/// Explicit simulation-only Account mutation used by the simulated Capital
/// rail. Live Account applications reject this command.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedCapitalMutation {
    pub mutation_id: IdempotencyKey,
    pub segment_key: SegmentKey,
    pub asset: Currency,
    pub amount: Quantity,
    pub kind: SimulatedCapitalMutationKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub product_id: Option<String>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedCapitalMutationQuery {
    pub mutation_id: IdempotencyKey,
    pub segment_key: SegmentKey,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SimulatedCapitalMutationStatus {
    Applied,
    NotFound,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulatedCapitalMutationStatusResponse {
    pub status: SimulatedCapitalMutationStatus,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarkToMarketRequest {
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub quote_asset: Currency,
    pub mark_price: Price,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AdvanceAccountTimeRequest {
    pub event_time_unix_nanos: UnixNanos,
}

#[cfg(test)]
mod tests {
    use super::{AdvanceAccountTimeRequest, DecimalValue, MarkToMarketRequest};

    #[test]
    fn simulation_controls_have_typed_contract_shapes() {
        let mark = MarkToMarketRequest {
            segment_key: kairos_primitives::account::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            quote_asset: kairos_primitives::reference::Currency::new("USDT").unwrap(),
            mark_price: kairos_primitives::decimal::Price::new(6_400_025, 2).unwrap(),
            observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(10),
        };
        assert_eq!(
            serde_json::to_value(mark).unwrap(),
            serde_json::json!({
                "segment_key": "spot",
                "instrument_id": "instrument:btc",
                "quote_asset": "USDT",
                "mark_price": "64000.25",
                "observed_at_unix_nanos": 10
            })
        );
        assert_eq!(
            serde_json::to_value(AdvanceAccountTimeRequest {
                event_time_unix_nanos: kairos_primitives::time::UnixNanos::new(11)
            })
            .unwrap(),
            serde_json::json!({"event_time_unix_nanos": 11})
        );
    }

    #[test]
    fn decimal_contract_enforces_shared_scale_limit() {
        let too_precise = format!("\"0.{}1\"", "0".repeat(18));
        assert!(serde_json::from_str::<DecimalValue>(&too_precise).is_err());

        assert!(DecimalValue::new(1, kairos_primitives::decimal::MAX_DECIMAL_SCALE + 1).is_err());
    }
}
