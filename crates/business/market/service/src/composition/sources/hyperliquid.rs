use kairos_workspace::{
    WorkspaceHyperliquidMarketType, WorkspaceMarketSourceBinding, WorkspacePublicMarketTransport,
};

use crate::MarketApplication;

use super::super::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source, default_endpoint,
};
use super::positive_interval;

pub(super) fn attach(
    application: &mut MarketApplication,
    source_id: &str,
    binding: &WorkspaceMarketSourceBinding,
) -> Result<(), String> {
    let WorkspaceMarketSourceBinding::Hyperliquid {
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
        WorkspaceHyperliquidMarketType::Spot => "spot",
        WorkspaceHyperliquidMarketType::Perpetual => "perpetual",
    };
    match transport {
        WorkspacePublicMarketTransport::Websocket => attach_hyperliquid_live_source(
            application,
            source_id,
            market_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("hyperliquid-websocket").to_owned()),
        ),
        WorkspacePublicMarketTransport::Rest => attach_hyperliquid_snapshot_source(
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
