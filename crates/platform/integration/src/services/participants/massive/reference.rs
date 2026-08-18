//! Massive instrument normalization without Reference-owned canonical IDs.

use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};

use crate::{
    ExternalInstrument, ExternalInstrumentCatalog, ExternalInstrumentKind, IntegrationError,
    ParticipantKind, ParticipantRef,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MassiveMarketRow {
    pub(crate) ticker: String,
    pub(crate) exchange: Option<String>,
    pub(crate) market_type: String,
    pub(crate) base: Option<String>,
    pub(crate) quote: Option<String>,
    pub(crate) active: bool,
    pub(crate) price_tick: Option<String>,
    pub(crate) amount_tick: Option<String>,
    pub(crate) price_precision: i32,
    pub(crate) amount_precision: i32,
    pub(crate) underlying: Option<String>,
    pub(crate) expiry_unix_nanos: Option<u64>,
    pub(crate) strike: Option<String>,
    pub(crate) option_right: Option<String>,
    pub(crate) contract_size: Option<String>,
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
                    .map(ParticipantSymbol::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
            };
            Ok(ExternalInstrument {
                source_symbol: ParticipantSymbol::new(row.ticker)
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
    use crate::ExternalInstrumentKind;

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
