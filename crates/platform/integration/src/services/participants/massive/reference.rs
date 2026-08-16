//! Massive instrument normalization without Reference-owned canonical IDs.

use kairos_primitives::{Currency, ProviderSymbol, UnixNanos};

use crate::application::capabilities::reference::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind,
};
use crate::application::capabilities::{ParticipantKind, ParticipantRef};
use crate::application::IntegrationError;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MassiveMarketRow {
    pub ticker: String,
    pub exchange: Option<String>,
    pub market_type: String,
    pub base: Option<String>,
    pub quote: Option<String>,
    pub active: bool,
    pub price_tick: Option<String>,
    pub amount_tick: Option<String>,
    pub price_precision: i32,
    pub amount_precision: i32,
    pub underlying: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub contract_size: Option<String>,
}

pub trait MassiveMarketClient: Send {
    fn load_markets(&mut self) -> Result<Vec<MassiveMarketRow>, String>;

    fn load_markets_page(
        &mut self,
        _cursor: Option<&str>,
        _limit: usize,
    ) -> Result<super::connection::MassiveMarketPage, String> {
        Ok(super::connection::MassiveMarketPage {
            rows: self.load_markets()?,
            next_cursor: None,
            complete: true,
        })
    }
}

pub(crate) fn normalize(
    rows: Vec<MassiveMarketRow>,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    let instruments = rows
        .into_iter()
        .map(|row| {
            let kind = match row.market_type.trim().to_ascii_lowercase().as_str() {
                "" | "equity" | "stocks" => ExternalInstrumentKind::Equity,
                "option" | "options" => ExternalInstrumentKind::Option,
                value => {
                    return Err(IntegrationError::InvalidPayload(format!(
                        "unsupported Massive instrument type: {value}"
                    )))
                }
            };
            if row.ticker.trim().is_empty() {
                return Err(IntegrationError::InvalidPayload(
                    "Massive ticker is required".into(),
                ));
            }
            let currency = |value: Option<String>| {
                value
                    .map(Currency::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
            };
            let symbol = |value: Option<String>| {
                value
                    .map(ProviderSymbol::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
            };
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(row.ticker)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: row.exchange.filter(|value| !value.trim().is_empty()),
                kind,
                // Equity/option underlyings are symbols, not currencies.
                base_currency: None,
                quote_currency: currency(row.quote)?,
                settlement_currency: None,
                underlying: symbol(row.underlying.or(row.base))?,
                expiry_unix_nanos: row.expiry_unix_nanos.map(UnixNanos::new),
                strike: row.strike,
                option_right: row.option_right,
                active: row.active,
                price_tick: row.price_tick,
                quantity_tick: row.amount_tick,
                minimum_quantity: None,
                minimum_notional: None,
                contract_value: row.contract_size,
                price_precision: u32::try_from(row.price_precision).ok(),
                quantity_precision: u32::try_from(row.amount_precision).ok(),
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::DataProvider, "massive")
            .expect("static Massive participant"),
        instruments,
    })
}

#[cfg(test)]
mod tests {
    use super::{normalize, MassiveMarketRow};
    use crate::application::capabilities::reference::ExternalInstrumentKind;

    #[test]
    fn preserves_massive_venue_and_option_facts_without_canonical_ids() {
        let facts = normalize(vec![MassiveMarketRow {
            ticker: "O:SPY260821C00500000".into(),
            exchange: Some("XNAS".into()),
            market_type: "options".into(),
            base: Some("SPY".into()),
            quote: Some("USD".into()),
            active: true,
            price_tick: Some("0.01".into()),
            amount_tick: Some("1".into()),
            price_precision: 2,
            amount_precision: 0,
            underlying: Some("SPY".into()),
            expiry_unix_nanos: Some(1_800_000_000_000_000_000),
            strike: Some("500".into()),
            option_right: Some("call".into()),
            contract_size: Some("100".into()),
        }])
        .expect("provider facts");
        let instrument = &facts.instruments[0];
        assert_eq!(instrument.kind, ExternalInstrumentKind::Option);
        assert_eq!(instrument.source_venue.as_deref(), Some("XNAS"));
        assert_eq!(instrument.underlying.as_ref().unwrap().as_str(), "SPY");
    }
}
