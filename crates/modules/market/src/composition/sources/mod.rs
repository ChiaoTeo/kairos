//! Provider-native Market source composition.
//!
//! This layer translates typed Market-owned bindings into concrete Integration
//! capabilities. Provider selection ends here and never enters the Actor or
//! server binary.

mod activation;
mod connections;
mod replay;
mod routing;

pub(crate) use connections::install as install_connections;
pub(crate) use routing::{binding_provider_product, binding_supports_canonical_market};

pub(crate) use activation::{attach_binance_snapshot, attach_stream};
pub use activation::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source,
    attach_massive_market_source, attach_okx_live_source, attach_okx_snapshot_source,
    default_endpoint, MarketProduct,
};
pub use replay::{
    attach_replay_source, attach_replay_source_with_checkpoint, attach_replay_source_with_policy,
};

pub(super) fn positive_interval(
    source_id: &str,
    milliseconds: u64,
) -> Result<std::time::Duration, String> {
    if milliseconds == 0 {
        return Err(format!(
            "Market source {source_id} snapshot_interval_ms must be positive"
        ));
    }
    Ok(std::time::Duration::from_millis(milliseconds))
}
