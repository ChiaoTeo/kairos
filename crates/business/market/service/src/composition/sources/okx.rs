use super::super::config::{
    MarketSourceBinding, OkxInstrumentType as ConfiguredOkxInstrumentType, PublicMarketTransport,
};
use kairos_integration::participants::okx::InstrumentType as OkxInstrumentType;

use crate::MarketApplication;

use super::super::{attach_okx_live_source, attach_okx_snapshot_source, default_endpoint};
use super::positive_interval;

pub(super) fn attach(
    application: &mut MarketApplication,
    source_id: &str,
    binding: &MarketSourceBinding,
) -> Result<(), String> {
    let MarketSourceBinding::Okx {
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
        ConfiguredOkxInstrumentType::Spot => ("spot", OkxInstrumentType::Spot),
        ConfiguredOkxInstrumentType::Swap => ("swap", OkxInstrumentType::Swap),
        ConfiguredOkxInstrumentType::Futures => ("futures", OkxInstrumentType::Futures),
        ConfiguredOkxInstrumentType::Options => ("options", OkxInstrumentType::Option),
    };
    match transport {
        PublicMarketTransport::Websocket => attach_okx_live_source(
            application,
            source_id,
            market_type,
            endpoint
                .clone()
                .unwrap_or_else(|| default_endpoint("okx-public-websocket").to_owned()),
        ),
        PublicMarketTransport::Rest => attach_okx_snapshot_source(
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
