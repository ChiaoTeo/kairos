use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::EventProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, EventMetadata};
use kairos_protocol::generated::kairos::reference::v_2 as fb;

use crate::ContractResult;
use crate::catalog::{Asset, Exchange, Instrument, Listing, Market};

#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub common: EventProtocolContext,
    pub catalog_revision: kairos_primitives::time::Generation,
}

impl std::ops::Deref for EncodeContext {
    type Target = EventProtocolContext;
    fn deref(&self) -> &Self::Target {
        &self.common
    }
}

impl EncodeContext {
    pub fn event(
        producer_id: impl Into<String>,
        producer_incarnation: u64,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
        catalog_revision: u64,
    ) -> Result<Self, String> {
        Ok(Self {
            common: EventProtocolContext::new(
                producer_id,
                producer_incarnation,
                identity,
                sequence,
                event_id,
            )?,
            catalog_revision: catalog_revision.into(),
        })
    }
}

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    kairos_protocol::metadata::event_metadata(
        builder,
        context,
        "reference.events",
        occurred_at_unix_nanos,
    )
}
/// Typed v2 Reference event encoder. It accepts contract-owned record models
/// and never serializes a record through JSON.
pub struct ReferenceEncoder;

impl ReferenceEncoder {
    pub fn exchange_upserted(
        record: &Exchange,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_exchange(record, context, occurred_at_unix_nanos, false)
    }
    pub fn exchange_updated(
        record: &Exchange,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_exchange(record, context, occurred_at_unix_nanos, true)
    }
    pub fn asset_upserted(
        record: &Asset,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_asset(record, context, occurred_at_unix_nanos, false)
    }

    pub fn asset_updated(
        record: &Asset,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_asset(record, context, occurred_at_unix_nanos, true)
    }

    pub fn instrument_upserted(
        record: &Instrument,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_instrument(record, context, occurred_at_unix_nanos, false)
    }

    pub fn instrument_updated(
        record: &Instrument,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_instrument(record, context, occurred_at_unix_nanos, true)
    }

    pub fn listing_upserted(
        record: &Listing,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_listing(record, context, occurred_at_unix_nanos, false)
    }

    pub fn listing_updated(
        record: &Listing,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_listing(record, context, occurred_at_unix_nanos, true)
    }

    pub fn market_upserted(
        record: &Market,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_market(record, context, occurred_at_unix_nanos, false)
    }

    pub fn market_updated(
        record: &Market,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_market(record, context, occurred_at_unix_nanos, true)
    }
}

fn encode_exchange(
    record: &Exchange,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let args = fb::ExchangeArgs {
        exchange_id: Some(builder.create_string(record.exchange_id.as_str())),
        name: Some(builder.create_string(&record.name)),
        status: status(record.status),
    };
    let exchange = fb::Exchange::create(&mut builder, &args);
    if updated {
        let root = fb::ExchangeUpdated::create(
            &mut builder,
            &fb::ExchangeUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                exchange: Some(exchange),
            },
        );
        fb::finish_exchange_updated_buffer(&mut builder, root);
    } else {
        let root = fb::ExchangeUpserted::create(
            &mut builder,
            &fb::ExchangeUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                exchange: Some(exchange),
            },
        );
        fb::finish_exchange_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_asset(
    record: &Asset,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let args = fb::AssetArgs {
        asset_id: Some(builder.create_string(&record.asset_id)),
        code: Some(builder.create_string(&record.code)),
        name: optional_string(&mut builder, record.name.as_deref()),
        asset_class: Some(builder.create_string(record.asset_class.as_str())),
        status: status(record.status),
    };
    let asset = fb::Asset::create(&mut builder, &args);
    if updated {
        let root = fb::AssetUpdated::create(
            &mut builder,
            &fb::AssetUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                asset: Some(asset),
            },
        );
        fb::finish_asset_updated_buffer(&mut builder, root);
    } else {
        let root = fb::AssetUpserted::create(
            &mut builder,
            &fb::AssetUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                asset: Some(asset),
            },
        );
        fb::finish_asset_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_instrument(
    record: &Instrument,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let instrument_id = builder.create_string(&record.instrument_id);
    let symbol = builder.create_string(&record.symbol);
    let name = optional_string(&mut builder, record.name.as_deref());
    let instrument_type = builder.create_string(record.instrument_type.as_str());
    let product_family = optional_string(&mut builder, record.product_family.as_deref());
    let underlying_instrument_id =
        optional_string(&mut builder, record.underlying_instrument_id.as_deref());
    let option_right = optional_string(&mut builder, record.option_right.as_deref());
    let issuer_id = optional_string(&mut builder, record.issuer_id.as_deref());
    let share_class = optional_string(&mut builder, record.share_class.as_deref());
    let primary_currency_asset_id =
        optional_string(&mut builder, record.primary_currency_asset_id.as_deref());
    let instrument_args = fb::InstrumentArgs {
        instrument_id: Some(instrument_id),
        symbol: Some(symbol),
        name,
        instrument_type: Some(instrument_type),
        product_family,
        underlying_instrument_id,
        expiry_unix_nanos: record.expiry_unix_nanos.unwrap_or_default().get(),
        option_right,
        issuer_id,
        share_class,
        primary_currency_asset_id,
        status: status(record.status),
        ..Default::default()
    };
    let instrument = fb::Instrument::create(&mut builder, &instrument_args);
    if updated {
        let root = fb::InstrumentUpdated::create(
            &mut builder,
            &fb::InstrumentUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                instrument: Some(instrument),
            },
        );
        fb::finish_instrument_updated_buffer(&mut builder, root);
    } else {
        let root = fb::InstrumentUpserted::create(
            &mut builder,
            &fb::InstrumentUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                instrument: Some(instrument),
            },
        );
        fb::finish_instrument_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_listing(
    record: &Listing,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let listing_id = builder.create_string(&record.listing_id);
    let instrument_id = builder.create_string(&record.instrument_id);
    let exchange_id = builder.create_string(&record.exchange_id);
    let exchange_symbol = builder.create_string(&record.exchange_symbol);
    let listing_args = fb::ListingArgs {
        listing_id: Some(listing_id),
        instrument_id: Some(instrument_id),
        exchange_id: Some(exchange_id),
        exchange_symbol: Some(exchange_symbol),
        status: status(record.status),
        effective_from_unix_nanos: record.effective_from_unix_nanos.get(),
        effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default().get(),
    };
    let listing = fb::Listing::create(&mut builder, &listing_args);
    if updated {
        let root = fb::ListingUpdated::create(
            &mut builder,
            &fb::ListingUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                listing: Some(listing),
            },
        );
        fb::finish_listing_updated_buffer(&mut builder, root);
    } else {
        let root = fb::ListingUpserted::create(
            &mut builder,
            &fb::ListingUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                listing: Some(listing),
            },
        );
        fb::finish_listing_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_market(
    record: &Market,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let price_tick = record
        .price_tick
        .map(|value| decimal(value.mantissa(), value.scale()));
    let quantity_tick = record
        .quantity_tick
        .map(|value| decimal(value.mantissa(), value.scale()));
    let minimum_quantity = record
        .minimum_quantity
        .map(|value| decimal(value.mantissa(), value.scale()));
    let minimum_notional = record
        .minimum_notional
        .map(|value| decimal(value.mantissa(), value.scale()));
    let contract_size = record
        .contract_size
        .map(|value| decimal(value.mantissa(), value.scale()));
    let market_id = builder.create_string(&record.market_id);
    let instrument_id = builder.create_string(&record.instrument_id);
    let listing_id = optional_string(&mut builder, record.listing_id.as_deref());
    let exchange_id = builder.create_string(&record.exchange_id);
    let instrument_kind = builder.create_string(record.instrument_kind.as_str());
    let venue_symbol = optional_string(&mut builder, record.venue_symbol.as_deref());
    let base_asset_id = optional_string(&mut builder, record.base_asset_id.as_deref());
    let quote_asset_id = optional_string(&mut builder, record.quote_asset_id.as_deref());
    let asset_type = optional_string(
        &mut builder,
        record.asset_type.as_ref().map(|value| value.as_str()),
    );
    let underlying_instrument_id =
        optional_string(&mut builder, record.underlying_instrument_id.as_deref());
    let market_args = fb::MarketArgs {
        market_id: Some(market_id),
        instrument_id: Some(instrument_id),
        listing_id,
        exchange_id: Some(exchange_id),
        instrument_kind: Some(instrument_kind),
        venue_symbol,
        base_asset_id,
        quote_asset_id,
        status: status(record.status),
        price_tick: price_tick.as_ref(),
        quantity_tick: quantity_tick.as_ref(),
        price_precision: record.price_precision,
        quantity_precision: record.quantity_precision,
        minimum_quantity: minimum_quantity.as_ref(),
        minimum_notional: minimum_notional.as_ref(),
        contract_size: contract_size.as_ref(),
        effective_from_unix_nanos: record.effective_from_unix_nanos.get(),
        effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default().get(),
        asset_type,
        underlying_instrument_id,
    };
    let market = fb::Market::create(&mut builder, &market_args);
    if updated {
        let root = fb::MarketUpdated::create(
            &mut builder,
            &fb::MarketUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                market: Some(market),
            },
        );
        fb::finish_market_updated_buffer(&mut builder, root);
    } else {
        let root = fb::MarketUpserted::create(
            &mut builder,
            &fb::MarketUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                market: Some(market),
            },
        );
        fb::finish_market_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn optional_string<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    value: Option<&str>,
) -> Option<WIPOffset<&'a str>> {
    value.map(|value| builder.create_string(value))
}

fn status(value: kairos_primitives::reference::ReferenceStatus) -> fb::ReferenceLifecycleStatus {
    match value {
        kairos_primitives::reference::ReferenceStatus::Draft => fb::ReferenceLifecycleStatus::DRAFT,
        kairos_primitives::reference::ReferenceStatus::Active => {
            fb::ReferenceLifecycleStatus::ACTIVE
        },
        kairos_primitives::reference::ReferenceStatus::Trading => {
            fb::ReferenceLifecycleStatus::TRADING
        },
        kairos_primitives::reference::ReferenceStatus::Suspended => {
            fb::ReferenceLifecycleStatus::SUSPENDED
        },
        kairos_primitives::reference::ReferenceStatus::Inactive => {
            fb::ReferenceLifecycleStatus::INACTIVE
        },
        kairos_primitives::reference::ReferenceStatus::Retired
        | kairos_primitives::reference::ReferenceStatus::Delisted => {
            fb::ReferenceLifecycleStatus::RETIRED
        },
        kairos_primitives::reference::ReferenceStatus::Expired => {
            fb::ReferenceLifecycleStatus::EXPIRED
        },
        kairos_primitives::reference::ReferenceStatus::Unknown => {
            fb::ReferenceLifecycleStatus::UNSPECIFIED
        },
    }
}

fn decimal(mantissa: i64, scale: u8) -> Decimal64 {
    Decimal64::new(mantissa, scale)
}
