//! Contract-owned administrative commands and application mappings.

pub use kairos_reference_contract::{
    UpsertAssetRequest as UpsertAssetCommand, UpsertInstrumentRequest as UpsertInstrumentCommand,
    UpsertListingRequest as UpsertListingCommand,
};

impl From<UpsertAssetCommand> for crate::domain::Asset {
    fn from(value: UpsertAssetCommand) -> Self {
        Self {
            source_id: None,
            asset_id: value.asset_id,
            code: value.code,
            name: value.name,
            asset_class: value.asset_class,
            status: value.status,
        }
    }
}

impl From<UpsertInstrumentCommand> for crate::domain::Instrument {
    fn from(value: UpsertInstrumentCommand) -> Self {
        Self {
            source_id: None,
            instrument_id: value.instrument_id,
            symbol: value.symbol,
            name: value.name,
            instrument_type: value.instrument_type,
            issuer_id: value.issuer_id,
            share_class: value.share_class,
            primary_currency_asset_id: value.primary_currency_asset_id,
            underlying_instrument_id: value.underlying_instrument_id,
            expiry_unix_nanos: value.expiry_unix_nanos,
            strike: value.strike,
            option_right: value.option_right,
            status: value.status,
        }
    }
}

impl From<UpsertListingCommand> for crate::domain::Listing {
    fn from(value: UpsertListingCommand) -> Self {
        Self {
            listing_id: value.listing_id,
            instrument_id: value.instrument_id,
            exchange_id: value.exchange_id,
            exchange_symbol: value.exchange_symbol,
            status: value.status,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos,
        }
    }
}
