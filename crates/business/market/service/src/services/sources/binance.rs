//! Binance-specific live recovery policy.
//!
//! Normal event/channel mechanics remain shared in `stream`; Binance depth
//! gap diagnostics carry the affected provider symbol, allowing this driver
//! to request a single-market snapshot barrier without restarting the source.

use kairos_integration::participants::binance::BinanceAsyncMarket;

use super::stream::{spawn_stream_with_policy, StreamFailurePolicy};
use super::SourceHandle;
use crate::domain::source::SourceDescriptor;

pub(crate) fn spawn_binance(
    descriptor: SourceDescriptor,
    connection: BinanceAsyncMarket,
    input_capacity: usize,
) -> SourceHandle {
    spawn_stream_with_policy(
        descriptor,
        connection,
        input_capacity,
        StreamFailurePolicy::MarketScopedResync,
    )
}
