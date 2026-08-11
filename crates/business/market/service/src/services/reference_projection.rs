use std::path::PathBuf;

use kairos_domain_types::Sequence;

use crate::domain::market::MarketDescriptor;

/// Private owner of the Reference current-state join used by Market.
pub(crate) struct ReferenceProjection {
    snapshot_path: Option<PathBuf>,
    markets: Option<Vec<MarketDescriptor>>,
    event_sequence: Option<Sequence>,
    recovery_needed: bool,
}

impl ReferenceProjection {
    pub(crate) fn new() -> Self {
        Self {
            snapshot_path: None,
            markets: None,
            event_sequence: None,
            recovery_needed: false,
        }
    }

    pub(crate) fn configure(&mut self, path: impl Into<PathBuf>) {
        self.snapshot_path = Some(path.into());
        self.recovery_needed = true;
    }

    pub(crate) fn require_recovery(&mut self, sequence: Option<Sequence>) {
        if let Some(sequence) = sequence {
            self.event_sequence = Some(self.event_sequence.unwrap_or_default().max(sequence));
        }
        self.recovery_needed = true;
    }

    pub(crate) fn recovery_needed(&self) -> bool {
        self.recovery_needed
    }

    pub(crate) fn markets(&self) -> Option<&[MarketDescriptor]> {
        self.markets.as_deref()
    }

    #[cfg(test)]
    pub(crate) fn event_sequence(&self) -> Option<Sequence> {
        self.event_sequence
    }

    pub(crate) fn recover(&mut self) -> Result<Vec<MarketDescriptor>, String> {
        if !self.recovery_needed {
            return Ok(self.markets.clone().unwrap_or_default());
        }
        let path = self
            .snapshot_path
            .as_ref()
            .ok_or_else(|| "Reference snapshot is not configured".to_string())?;
        let snapshot = kairos_reference_contract::ReferenceMmapMarketsReader::open(
            path,
            "reference",
            "reference.lifecycle",
        )
        .map_err(|error| error.to_string())?
        .read()
        .map_err(|error| error.to_string())?;
        if let Some(expected) = self.event_sequence {
            if snapshot.event_sequence < expected.get() {
                return Err(format!(
                    "Reference snapshot event sequence {} is behind required sequence {}",
                    snapshot.event_sequence,
                    expected.get()
                ));
            }
        }
        let markets = snapshot
            .markets
            .into_iter()
            .filter(|market| matches!(market.status.as_str(), "active" | "trading"))
            .map(|market| {
                let mut descriptor = MarketDescriptor::new(
                    market.market_id,
                    market.instrument_id,
                    market.exchange_id,
                    market.market_type,
                    market.source_symbol,
                )?;
                descriptor.asset_type = market.asset_type;
                descriptor.underlying_instrument_id = market.underlying_instrument_id;
                Ok(descriptor)
            })
            .collect::<Result<Vec<_>, String>>()?;
        self.event_sequence = Some(snapshot.event_sequence.into());
        self.markets = Some(markets.clone());
        self.recovery_needed = false;
        Ok(markets)
    }
}
