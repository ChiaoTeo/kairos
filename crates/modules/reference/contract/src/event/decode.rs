use kairos_protocol::generated::kairos::reference::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind};

use crate::{ContractError, ContractResult};

pub enum ReferenceEvent<'a> {
    ExchangeUpserted(fb::ExchangeUpserted<'a>),
    ExchangeUpdated(fb::ExchangeUpdated<'a>),
    AssetUpserted(fb::AssetUpserted<'a>),
    AssetUpdated(fb::AssetUpdated<'a>),
    InstrumentUpserted(fb::InstrumentUpserted<'a>),
    InstrumentUpdated(fb::InstrumentUpdated<'a>),
    ListingUpserted(fb::ListingUpserted<'a>),
    ListingUpdated(fb::ListingUpdated<'a>),
    MarketUpserted(fb::MarketUpserted<'a>),
    MarketUpdated(fb::MarketUpdated<'a>),
}

pub type ReferenceEventView<'a> = ReferenceEvent<'a>;

impl<'a> ReferenceEvent<'a> {
    pub fn kind(&self) -> ReferenceEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceEventKind {
    ExchangeUpserted,
    ExchangeUpdated,
    AssetUpserted,
    AssetUpdated,
    InstrumentUpserted,
    InstrumentUpdated,
    ListingUpserted,
    ListingUpdated,
    MarketUpserted,
    MarketUpdated,
}

impl ReferenceEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ExchangeUpserted => "exchange_upserted",
            Self::ExchangeUpdated => "exchange_updated",
            Self::AssetUpserted => "asset_upserted",
            Self::AssetUpdated => "asset_updated",
            Self::InstrumentUpserted => "instrument_upserted",
            Self::InstrumentUpdated => "instrument_updated",
            Self::ListingUpserted => "listing_upserted",
            Self::ListingUpdated => "listing_updated",
            Self::MarketUpserted => "market_upserted",
            Self::MarketUpdated => "market_updated",
        }
    }
}

impl BusinessEventKind for ReferenceEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

impl<'a> BorrowedEventView<'a> for ReferenceEvent<'a> {
    type Kind = ReferenceEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::ExchangeUpserted(_) => ReferenceEventKind::ExchangeUpserted,
            Self::ExchangeUpdated(_) => ReferenceEventKind::ExchangeUpdated,
            Self::AssetUpserted(_) => ReferenceEventKind::AssetUpserted,
            Self::AssetUpdated(_) => ReferenceEventKind::AssetUpdated,
            Self::InstrumentUpserted(_) => ReferenceEventKind::InstrumentUpserted,
            Self::InstrumentUpdated(_) => ReferenceEventKind::InstrumentUpdated,
            Self::ListingUpserted(_) => ReferenceEventKind::ListingUpserted,
            Self::ListingUpdated(_) => ReferenceEventKind::ListingUpdated,
            Self::MarketUpserted(_) => ReferenceEventKind::MarketUpserted,
            Self::MarketUpdated(_) => ReferenceEventKind::MarketUpdated,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::ExchangeUpserted(value) => value.metadata(),
            Self::ExchangeUpdated(value) => value.metadata(),
            Self::AssetUpserted(value) => value.metadata(),
            Self::AssetUpdated(value) => value.metadata(),
            Self::InstrumentUpserted(value) => value.metadata(),
            Self::InstrumentUpdated(value) => value.metadata(),
            Self::ListingUpserted(value) => value.metadata(),
            Self::ListingUpdated(value) => value.metadata(),
            Self::MarketUpserted(value) => value.metadata(),
            Self::MarketUpdated(value) => value.metadata(),
        }
    }
}

pub fn decode_event(bytes: &[u8]) -> ContractResult<ReferenceEvent<'_>> {
    if bytes.len() < 8 {
        return Err(ContractError::Invalid(
            "unknown Reference v2 event identifier".into(),
        ));
    }
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
        exchange_upserted_buffer_has_identifier,
        root_as_exchange_upserted,
        ExchangeUpserted
    );
    decode!(
        exchange_updated_buffer_has_identifier,
        root_as_exchange_updated,
        ExchangeUpdated
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
