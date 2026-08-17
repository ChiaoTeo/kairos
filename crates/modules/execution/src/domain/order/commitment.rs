//! Capacity commitments held around exchange-facing orders.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentResource {
    Asset(Currency),
    Instrument(InstrumentId),
    MarginNotional(Currency),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentBasis {
    QuotePriceCap {
        price_cap: Price,
    },
    BaseQuantity,
    ContractNotional {
        price_cap: Price,
        contract_size: Quantity,
    },
    SimulationQuantity,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CommitmentStatus {
    HeldBeforeSend,
    Active,
    Uncertain,
    Reduced,
    Released,
    Reconciled,
}

impl CommitmentStatus {
    pub fn consumes_capacity(self) -> bool {
        matches!(
            self,
            Self::HeldBeforeSend | Self::Active | Self::Uncertain | Self::Reduced
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct OrderCommitment {
    pub order_id: OrderId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub resource: CommitmentResource,
    pub amount: Money,
    pub remaining_quantity: Quantity,
    pub status: CommitmentStatus,
    pub basis: CommitmentBasis,
    #[serde(default)]
    pub settlement_asset: Option<Currency>,
    pub updated_at_unix_nanos: UnixNanos,
}

impl OrderCommitment {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: OrderId,
        account_id: AccountId,
        segment_key: SegmentKey,
        instrument_id: InstrumentId,
        side: OrderSide,
        resource: CommitmentResource,
        amount: Money,
        remaining_quantity: Quantity,
        basis: CommitmentBasis,
        updated_at_unix_nanos: UnixNanos,
    ) -> Result<Self, String> {
        if amount <= Money::ZERO || remaining_quantity <= Quantity::ZERO {
            return Err("order commitment amount and quantity must be positive".into());
        }
        Ok(Self {
            order_id,
            account_id,
            segment_key,
            instrument_id,
            side,
            resource,
            amount,
            remaining_quantity,
            status: CommitmentStatus::HeldBeforeSend,
            basis,
            settlement_asset: None,
            updated_at_unix_nanos,
        })
    }
}
