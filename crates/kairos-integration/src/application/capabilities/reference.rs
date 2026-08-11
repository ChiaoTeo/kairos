//! Reference-facing integration capability.

use crate::application::IntegrationError;
use crate::domain::ParticipantRef;
use kairos_domain_types::{Currency, ProviderSymbol, UnixNanos};
use std::future::Future;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExternalInstrumentKind {
    Equity,
    /// A perpetual derivative whose economic underlying is an equity rather
    /// than a crypto asset. The provider-specific product spelling remains
    /// inside Integration; Reference uses this semantic distinction to build
    /// the correct canonical underlying and to avoid crypto/equity identity
    /// collisions.
    EquityPerpetual,
    Spot,
    Margin,
    Perpetual,
    Future,
    Option,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalInstrument {
    pub source_symbol: ProviderSymbol,
    /// Participant-native listing venue code (for example XNAS). Integration
    /// preserves it verbatim; Reference owns canonical exchange identity.
    pub source_venue: Option<String>,
    pub kind: ExternalInstrumentKind,
    pub base_currency: Option<Currency>,
    pub quote_currency: Option<Currency>,
    pub settlement_currency: Option<Currency>,
    pub underlying: Option<ProviderSymbol>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub active: bool,
    pub price_tick: Option<String>,
    pub quantity_tick: Option<String>,
    pub minimum_quantity: Option<String>,
    pub minimum_notional: Option<String>,
    pub contract_value: Option<String>,
    pub price_precision: Option<u32>,
    pub quantity_precision: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalInstrumentCatalog {
    pub participant: ParticipantRef,
    pub instruments: Vec<ExternalInstrument>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalInstrumentCatalogPage {
    pub catalog: ExternalInstrumentCatalog,
    pub next_cursor: Option<String>,
    pub complete: bool,
}

/// Provider instrument discovery. Returned facts deliberately contain no
/// canonical InstrumentId, ListingId, or MarketId; Reference owns those IDs.
pub trait InstrumentCatalogConnection: Send {
    fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError>;

    fn fetch_instruments_page(
        &mut self,
        _cursor: Option<&str>,
        _limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        Ok(ExternalInstrumentCatalogPage {
            catalog: self.fetch_instruments()?,
            next_cursor: None,
            complete: true,
        })
    }
}

/// Async-first catalog capability. The caller's runtime polls the returned
/// future; Integration does not create or retain a runtime.
pub trait AsyncInstrumentCatalogConnection: Send {
    fn fetch_instruments(
        &mut self,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalog, IntegrationError>> + Send;

    fn fetch_instruments_page(
        &mut self,
        _cursor: Option<&str>,
        _limit: usize,
    ) -> impl Future<Output = Result<ExternalInstrumentCatalogPage, IntegrationError>> + Send {
        async move {
            Ok(ExternalInstrumentCatalogPage {
                catalog: self.fetch_instruments().await?,
                next_cursor: None,
                complete: true,
            })
        }
    }
}
