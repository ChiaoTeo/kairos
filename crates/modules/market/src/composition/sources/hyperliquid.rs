use super::super::config::{HyperliquidMarketType, MarketSourceBinding, PublicMarketTransport};

use crate::MarketApplication;

use super::super::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source, default_endpoint,
};
use super::positive_interval;

pub(super) fn attach(
    application: &mut MarketApplication,
    source_id: &str,
    binding: &MarketSourceBinding,
) -> Result<(), String> {
    let MarketSourceBinding::Hyperliquid {
        market_type,
        transport,
        endpoint,
        snapshot_interval_ms,
        ..
    } = binding
    else {
        return Err(format!(
            "Market source {source_id} is not a Hyperliquid binding"
        ));
    };
    let market_type = match market_type {
        HyperliquidMarketType::Spot => "spot",
        HyperliquidMarketType::Perpetual => "perpetual",
    };
    match transport {
        PublicMarketTransport::Websocket => attach_hyperliquid_live_source(
            application,
            source_id,
            market_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("hyperliquid-websocket").to_owned()),
        ),
        PublicMarketTransport::Rest => attach_hyperliquid_snapshot_source(
            application,
            source_id,
            market_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("hyperliquid-info").to_owned()),
            positive_interval(source_id, *snapshot_interval_ms)?,
        ),
    }
}
