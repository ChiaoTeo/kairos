//! Participant instrument catalog facts consumed by Reference.

use crate::domain::ParticipantRef;
use kairos_primitives::{Currency, ParticipantSymbol, UnixNanos};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExternalInstrumentKind {
    Equity,
    /// A perpetual derivative whose economic underlying is an equity rather
    /// than a crypto asset. The participant-specific product spelling remains
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
    pub source_symbol: ParticipantSymbol,
    /// Participant-native listing venue code (for example XNAS). Integration
    /// preserves it verbatim; Reference owns canonical exchange identity.
    pub source_venue: Option<String>,
    pub kind: ExternalInstrumentKind,
    pub base_currency: Option<Currency>,
    pub quote_currency: Option<Currency>,
    pub settlement_currency: Option<Currency>,
    pub underlying: Option<ParticipantSymbol>,
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
