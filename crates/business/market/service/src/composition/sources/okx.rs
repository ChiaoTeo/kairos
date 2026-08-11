use kairos_integration::participants::okx::InstrumentType as OkxInstrumentType;
use kairos_workspace::{
    WorkspaceMarketSourceBinding, WorkspaceOkxInstrumentType, WorkspacePublicMarketTransport,
};

use crate::MarketApplication;

use super::super::{attach_okx_live_source, attach_okx_snapshot_source, default_endpoint};
use super::positive_interval;

pub(super) fn attach(
    application: &mut MarketApplication,
    source_id: &str,
    binding: &WorkspaceMarketSourceBinding,
) -> Result<(), String> {
    let WorkspaceMarketSourceBinding::Okx {
        instrument_type,
        transport,
        endpoint,
        snapshot_interval_ms,
        ..
    } = binding
    else {
        return Err(format!("Market source {source_id} is not an OKX binding"));
    };
    let (market_type, instrument_type) = match instrument_type {
        WorkspaceOkxInstrumentType::Spot => ("spot", OkxInstrumentType::Spot),
        WorkspaceOkxInstrumentType::Swap => ("swap", OkxInstrumentType::Swap),
        WorkspaceOkxInstrumentType::Futures => ("futures", OkxInstrumentType::Futures),
        WorkspaceOkxInstrumentType::Options => ("options", OkxInstrumentType::Option),
    };
    match transport {
        WorkspacePublicMarketTransport::Websocket => attach_okx_live_source(
            application,
            source_id,
            market_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("okx-public-websocket").to_owned()),
        ),
        WorkspacePublicMarketTransport::Rest => attach_okx_snapshot_source(
            application,
            source_id,
            market_type,
            "crypto",
            instrument_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("okx-spot-rest").to_owned()),
            positive_interval(source_id, *snapshot_interval_ms)?,
        ),
    }
}
