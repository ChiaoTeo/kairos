//! Application-owned administrative commands.

use kairos_primitives::{
    AssetClass, AssetId, Exchange, InstrumentId, InstrumentKind, IssuerId, ListingId,
    ReferenceStatus, Symbol, UnixNanos,
};
use serde::Deserialize;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct UpsertAssetCommand {
    pub asset_id: AssetId,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct UpsertInstrumentCommand {
    pub instrument_id: InstrumentId,
    pub symbol: Symbol,
    pub name: Option<String>,
    pub instrument_type: InstrumentKind,
    #[serde(default)]
    pub issuer_id: Option<IssuerId>,
    #[serde(default)]
    pub share_class: Option<String>,
    #[serde(default)]
    pub primary_currency_asset_id: Option<AssetId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct UpsertListingCommand {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub exchange_symbol: Symbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

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
            source_id: None,
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
