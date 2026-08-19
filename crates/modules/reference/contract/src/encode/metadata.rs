use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_protocol::generated::kairos::common::v_2::{
    Decimal64, EventMetadata, EventMetadataArgs,
};
use kairos_protocol::generated::kairos::reference::v_2 as fb;
use kairos_protocol::InstanceIdentity;

use crate::transport::{Asset, Entity, Instrument, Listing, Market};
use crate::{ContractError, ContractResult};

#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub producer_id: String,
    pub identity: InstanceIdentity,
    pub sequence: u64,
    pub event_id: String,
    pub catalog_revision: u64,
}

impl EncodeContext {
    pub fn event(
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
        catalog_revision: u64,
    ) -> Self {
        Self {
            producer_id: producer_id.into(),
            identity,
            sequence,
            event_id: event_id.into(),
            catalog_revision,
        }
    }
}

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let event_id = builder.create_string(&context.event_id);
    let stream_id = builder.create_string("reference.events");
    let producer_id = builder.create_string(&context.producer_id);
    let workspace_id = builder.create_string(&context.identity.workspace_id);
    let launch_id = non_empty(builder, &context.identity.launch_id);
    let instance_id = non_empty(builder, &context.identity.instance_id);
    EventMetadata::create(
        builder,
        &EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            sequence: context.sequence,
            producer_id: Some(producer_id),
            workspace_id: Some(workspace_id),
            launch_id,
            instance_id,
            occurred_at_unix_nanos,
            published_at_unix_nanos: now_unix_nanos(),
            ..Default::default()
        },
    )
}

/// Typed v2 Reference event encoder. It accepts contract-owned record models
/// and never serializes a record through JSON.
pub struct ReferenceEncoder;

impl ReferenceEncoder {
    pub fn entity_upserted(
        record: &Entity,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_entity(record, context, occurred_at_unix_nanos, false)
    }
    pub fn entity_updated(
        record: &Entity,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_entity(record, context, occurred_at_unix_nanos, true)
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

fn encode_entity(
    record: &Entity,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let args = fb::EntityArgs {
        entity_id: Some(builder.create_string(&record.entity_id)),
        entity_type: Some(builder.create_string(&record.entity_type)),
        name: Some(builder.create_string(&record.name)),
        status: status(&record.status)?,
    };
    let entity = fb::Entity::create(&mut builder, &args);
    if updated {
        let root = fb::EntityUpdated::create(
            &mut builder,
            &fb::EntityUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                entity: Some(entity),
            },
        );
        fb::finish_entity_updated_buffer(&mut builder, root);
    } else {
        let root = fb::EntityUpserted::create(
            &mut builder,
            &fb::EntityUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                entity: Some(entity),
            },
        );
        fb::finish_entity_upserted_buffer(&mut builder, root);
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
        status: status(&record.status)?,
    };
    let asset = fb::Asset::create(&mut builder, &args);
    if updated {
        let root = fb::AssetUpdated::create(
            &mut builder,
            &fb::AssetUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                asset: Some(asset),
            },
        );
        fb::finish_asset_updated_buffer(&mut builder, root);
    } else {
        let root = fb::AssetUpserted::create(
            &mut builder,
            &fb::AssetUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
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
        expiry_unix_nanos: record.expiry_unix_nanos.unwrap_or_default(),
        option_right,
        issuer_id,
        share_class,
        primary_currency_asset_id,
        status: status(&record.status)?,
        ..Default::default()
    };
    let instrument = fb::Instrument::create(&mut builder, &instrument_args);
    if updated {
        let root = fb::InstrumentUpdated::create(
            &mut builder,
            &fb::InstrumentUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                instrument: Some(instrument),
            },
        );
        fb::finish_instrument_updated_buffer(&mut builder, root);
    } else {
        let root = fb::InstrumentUpserted::create(
            &mut builder,
            &fb::InstrumentUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
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
        status: status(&record.status)?,
        effective_from_unix_nanos: record.effective_from_unix_nanos,
        effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default(),
    };
    let listing = fb::Listing::create(&mut builder, &listing_args);
    if updated {
        let root = fb::ListingUpdated::create(
            &mut builder,
            &fb::ListingUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                listing: Some(listing),
            },
        );
        fb::finish_listing_updated_buffer(&mut builder, root);
    } else {
        let root = fb::ListingUpserted::create(
            &mut builder,
            &fb::ListingUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
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
    let price_tick = decimal(record.price_tick.as_deref())?;
    let quantity_tick = decimal(record.quantity_tick.as_deref())?;
    let minimum_quantity = decimal(record.minimum_quantity.as_deref())?;
    let minimum_notional = decimal(record.minimum_notional.as_deref())?;
    let contract_size = decimal(record.contract_size.as_deref())?;
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
        status: status(&record.status)?,
        price_tick: price_tick.as_ref(),
        quantity_tick: quantity_tick.as_ref(),
        price_precision: record.price_precision,
        quantity_precision: record.quantity_precision,
        minimum_quantity: minimum_quantity.as_ref(),
        minimum_notional: minimum_notional.as_ref(),
        contract_size: contract_size.as_ref(),
        effective_from_unix_nanos: record.effective_from_unix_nanos,
        effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default(),
        asset_type,
        underlying_instrument_id,
    };
    let market = fb::Market::create(&mut builder, &market_args);
    if updated {
        let root = fb::MarketUpdated::create(
            &mut builder,
            &fb::MarketUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
                market: Some(market),
            },
        );
        fb::finish_market_updated_buffer(&mut builder, root);
    } else {
        let root = fb::MarketUpserted::create(
            &mut builder,
            &fb::MarketUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision,
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

fn status(value: &str) -> ContractResult<fb::ReferenceLifecycleStatus> {
    match value.to_ascii_lowercase().as_str() {
        "draft" => Ok(fb::ReferenceLifecycleStatus::DRAFT),
        "active" => Ok(fb::ReferenceLifecycleStatus::ACTIVE),
        "trading" => Ok(fb::ReferenceLifecycleStatus::TRADING),
        "suspended" => Ok(fb::ReferenceLifecycleStatus::SUSPENDED),
        "inactive" => Ok(fb::ReferenceLifecycleStatus::INACTIVE),
        "retired" | "delisted" => Ok(fb::ReferenceLifecycleStatus::RETIRED),
        "expired" => Ok(fb::ReferenceLifecycleStatus::EXPIRED),
        "" | "unspecified" => Ok(fb::ReferenceLifecycleStatus::UNSPECIFIED),
        other => Err(ContractError::Invalid(format!(
            "unknown Reference lifecycle status: {other}"
        ))),
    }
}

fn decimal(value: Option<&str>) -> ContractResult<Option<Decimal64>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value
        .trim()
        .parse::<kairos_primitives::DecimalParts>()
        .map_err(|error| ContractError::Invalid(error.to_string()))?;
    Ok(Some(Decimal64::new(value.mantissa(), value.scale())))
}

fn non_empty<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}
