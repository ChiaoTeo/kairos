use kairos_protocol::generated::kairos::reference::v_2 as fb;

use crate::{ContractError, ContractResult};

pub enum ReferenceEvent<'a> {
    EntityUpserted(fb::EntityUpserted<'a>),
    EntityUpdated(fb::EntityUpdated<'a>),
    AssetUpserted(fb::AssetUpserted<'a>),
    AssetUpdated(fb::AssetUpdated<'a>),
    InstrumentUpserted(fb::InstrumentUpserted<'a>),
    InstrumentUpdated(fb::InstrumentUpdated<'a>),
    ListingUpserted(fb::ListingUpserted<'a>),
    ListingUpdated(fb::ListingUpdated<'a>),
    MarketUpserted(fb::MarketUpserted<'a>),
    MarketUpdated(fb::MarketUpdated<'a>),
}

pub fn decode_event(bytes: &[u8]) -> ContractResult<ReferenceEvent<'_>> {
    macro_rules! decode {
        ($has:ident, $root:ident, $variant:ident) => {
            if fb::$has(bytes) {
                return fb::$root(bytes)
                    .map(ReferenceEvent::$variant)
                    .map_err(|error| ContractError::Invalid(error.to_string()));
            }
        };
    }
    decode!(
        entity_upserted_buffer_has_identifier,
        root_as_entity_upserted,
        EntityUpserted
    );
    decode!(
        entity_updated_buffer_has_identifier,
        root_as_entity_updated,
        EntityUpdated
    );
    decode!(
        asset_upserted_buffer_has_identifier,
        root_as_asset_upserted,
        AssetUpserted
    );
    decode!(
        asset_updated_buffer_has_identifier,
        root_as_asset_updated,
        AssetUpdated
    );
    decode!(
        instrument_upserted_buffer_has_identifier,
        root_as_instrument_upserted,
        InstrumentUpserted
    );
    decode!(
        instrument_updated_buffer_has_identifier,
        root_as_instrument_updated,
        InstrumentUpdated
    );
    decode!(
        listing_upserted_buffer_has_identifier,
        root_as_listing_upserted,
        ListingUpserted
    );
    decode!(
        listing_updated_buffer_has_identifier,
        root_as_listing_updated,
        ListingUpdated
    );
    decode!(
        market_upserted_buffer_has_identifier,
        root_as_market_upserted,
        MarketUpserted
    );
    decode!(
        market_updated_buffer_has_identifier,
        root_as_market_updated,
        MarketUpdated
    );
    Err(ContractError::Invalid(
        "unknown Reference v2 event identifier".into(),
    ))
}
