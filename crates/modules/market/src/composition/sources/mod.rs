//! Provider-native Market source composition.
//!
//! This layer translates typed Market-owned bindings into concrete Integration
//! capabilities. Provider selection ends here and never enters the Actor or
//! server binary.

mod activation;
mod binance;
mod hyperliquid;
mod massive;
mod okx;
mod replay;
mod routing;

pub(crate) use routing::{binding_provider_product, binding_supports_canonical_market};

pub(crate) use activation::ConfiguredMarketSourceActivator;
pub(crate) use activation::{
    attach_binance_snapshot, attach_binance_stream, attach_massive_source_with_id, attach_stream,
};
pub use activation::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source,
    attach_massive_market_source, attach_okx_live_source, attach_okx_snapshot_source,
    default_endpoint, MarketProduct,
};
pub use replay::{
    attach_replay_source, attach_replay_source_with_checkpoint, attach_replay_source_with_policy,
};

use std::path::Path;

use super::config::MarketSourceBinding;

use crate::MarketApplication;

pub(super) fn attach_configured(
    application: &mut MarketApplication,
    credentials_root: &Path,
    source_id: &str,
    binding: &MarketSourceBinding,
) -> Result<(), String> {
    match binding {
        MarketSourceBinding::BinanceSpot { .. }
        | MarketSourceBinding::BinanceEquity { .. }
        | MarketSourceBinding::BinanceDerivatives { .. } => {
            binance::attach(application, credentials_root, source_id, binding)
        }
        MarketSourceBinding::Massive { .. } => {
            massive::attach(application, credentials_root, source_id, binding)
        }
        MarketSourceBinding::Okx { .. } => okx::attach(application, source_id, binding),
        MarketSourceBinding::Hyperliquid { .. } => {
            hyperliquid::attach(application, source_id, binding)
        }
    }
}

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
