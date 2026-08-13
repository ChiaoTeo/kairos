use std::path::PathBuf;

use kairos_domain_types::Sequence;

use crate::domain::market::MarketDescriptor;

/// Private owner of the Reference current-state join used by Market.
pub(crate) struct ReferenceProjection {
    database_path: Option<PathBuf>,
    markets: Option<Vec<MarketDescriptor>>,
    event_sequence: Option<Sequence>,
    recovery_needed: bool,
}

impl ReferenceProjection {
    pub(crate) fn new() -> Self {
        Self {
            database_path: None,
            markets: None,
            event_sequence: None,
            recovery_needed: false,
        }
    }

    pub(crate) fn configure(&mut self, path: impl Into<PathBuf>) {
        self.database_path = Some(path.into());
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
            .database_path
            .as_ref()
            .ok_or_else(|| "Reference SQLite database is not configured".to_string())?;
        let reader = kairos_reference_contract::ReferenceSqliteReader::open(path)
            .map_err(|error| error.to_string())?;
        let mut query = kairos_reference_contract::SqliteMarketQuery {
            statuses: vec!["active".into(), "trading".into()],
            limit: 10_000,
            ..Default::default()
        };
        let mut markets = Vec::new();
        let mut watermark = None;
        loop {
            let page = reader
                .market_page(&query)
                .map_err(|error| error.to_string())?;
            if watermark.is_some_and(|expected| expected != page.watermark) {
                return Err(
                    "Reference SQLite watermark changed while rebuilding projection".into(),
                );
            }
            watermark = Some(page.watermark);
            let page_len = page.markets.len();
            query.after_market_id = page.markets.last().map(|market| market.market_id.clone());
            markets.extend(page.markets);
            if page_len < query.limit {
                break;
            }
        }
        let watermark = watermark.unwrap_or_default();
        if let Some(expected) = self.event_sequence {
            if watermark.event_sequence < expected.get() {
                return Err(format!(
                    "Reference SQLite event sequence {} is behind required sequence {}",
                    watermark.event_sequence,
                    expected.get()
                ));
            }
        }
        let markets = markets
            .into_iter()
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
        self.event_sequence = Some(watermark.event_sequence.into());
        self.markets = Some(markets.clone());
        self.recovery_needed = false;
        Ok(markets)
    }
}
