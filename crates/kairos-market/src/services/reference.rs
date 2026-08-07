//! Reference snapshot adapter used after a ReferenceChanged notification.

use kairos_protocol::generated::kairos::reference::v_1::{
    markets_snapshot_buffer_has_identifier, root_as_markets_snapshot,
};

use crate::application::market::protocol::ReferenceSnapshotReader;
use crate::application::market::wire::ReferenceChangeNotice;
use crate::domain::market::MarketDescriptor;

pub struct FlatbuffersReferenceSnapshotReader {
    payload: Vec<u8>,
}

impl FlatbuffersReferenceSnapshotReader {
    pub fn new(payload: &[u8]) -> Result<Self, String> {
        if !markets_snapshot_buffer_has_identifier(payload) {
            return Err("reference markets snapshot has an invalid file identifier".into());
        }
        root_as_markets_snapshot(payload)
            .map_err(|error| format!("invalid Reference MarketsSnapshot: {error}"))?;
        Ok(Self {
            payload: payload.to_vec(),
        })
    }
}

impl ReferenceSnapshotReader for FlatbuffersReferenceSnapshotReader {
    fn read_markets(
        &mut self,
        _notice: &ReferenceChangeNotice,
    ) -> Result<Vec<MarketDescriptor>, String> {
        let snapshot = root_as_markets_snapshot(&self.payload)
            .map_err(|error| format!("invalid Reference MarketsSnapshot: {error}"))?;
        let markets = snapshot
            .payload()
            .markets()
            .map(|values| {
                (0..values.len())
                    .map(|index| {
                        let market = values.get(index);
                        MarketDescriptor {
                            market_id: market.market_id().to_owned(),
                            instrument_id: market.instrument_id().to_owned(),
                            venue_id: market.venue_id().to_owned(),
                            market_type: market.market_type().to_owned(),
                            source_symbol: market.source_symbol().to_owned(),
                            status: market.status().to_owned(),
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(markets)
    }
}
