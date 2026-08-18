//! Binance-native ordinary-order extensions kept off the shared capability surface.

use kairos_primitives::ParticipantSymbol;

use crate::OrderEntryRequest;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceAmendOrderRequest {
    /// Full desired replacement state. Binance Futures modify requires side,
    /// quantity, and price rather than a sparse patch.
    pub replacement: OrderEntryRequest,
    pub remote_order_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceCancelOrderRequest {
    pub order: OrderEntryRequest,
    pub remote_order_id: String,
    pub at_unix_nanos: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceCancelAllScope {
    pub symbol: ParticipantSymbol,
}
