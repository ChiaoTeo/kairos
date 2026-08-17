use crate::{ContractError, ContractResult, ReferenceLatestSnapshot};
use flatbuffers::{FlatBufferBuilder, WIPOffset};
use kairos_protocol::generated::kairos::{
    common::v_2::{Decimal64, ViewCompleteness, ViewMetadata, ViewMetadataArgs},
    reference::v_2 as fb,
};
use kairos_transport::SnapshotEnvelopeMetadata;

pub fn encode_reference_latest(snapshot: &ReferenceLatestSnapshot) -> ContractResult<Vec<u8>> {
    let mut b = FlatBufferBuilder::new();
    let entities = snapshot
        .entities
        .iter()
        .map(|value| entity(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let assets = snapshot
        .assets
        .iter()
        .map(|value| asset(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let instruments = snapshot
        .instruments
        .iter()
        .map(|value| instrument(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let listings = snapshot
        .listings
        .iter()
        .map(|value| listing(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let markets = snapshot
        .markets
        .iter()
        .map(|value| market(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let products = snapshot
        .financial_products
        .iter()
        .map(|value| financial_product(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let execution_accesses = snapshot
        .execution_accesses
        .iter()
        .map(|value| execution_access(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let market_data_accesses = snapshot
        .market_data_accesses
        .iter()
        .map(|value| market_data_access(&mut b, value))
        .collect::<ContractResult<Vec<_>>>()?;
    let provider_health = snapshot
        .provider_health
        .iter()
        .map(|value| {
            let provider_id = b.create_string(&value.provider_id);
            let status = b.create_string(&value.status);
            let message = optional_string(&mut b, value.message.as_deref());
            fb::ProviderHealthState::create(
                &mut b,
                &fb::ProviderHealthStateArgs {
                    provider_id: Some(provider_id),
                    status: Some(status),
                    message,
                    updated_at_unix_nanos: value.updated_at_unix_nanos,
                },
            )
        })
        .collect::<Vec<_>>();
    let option_underlyings = snapshot
        .option_underlyings
        .iter()
        .map(|value| b.create_string(value))
        .collect::<Vec<_>>();
    let lifecycle_events = snapshot
        .lifecycle_events
        .iter()
        .map(|value| {
            let event_id = b.create_string(&value.event_id);
            let event_type = b.create_string(&value.event_type);
            let record_kind = optional_string(&mut b, value.record_kind.as_deref());
            let record_id = optional_string(&mut b, value.record_id.as_deref());
            fb::LifecycleEntry::create(
                &mut b,
                &fb::LifecycleEntryArgs {
                    event_id: Some(event_id),
                    event_type: Some(event_type),
                    event_time_unix_nanos: value.event_time_unix_nanos,
                    record_kind,
                    record_id,
                },
            )
        })
        .collect::<Vec<_>>();

    let entities = b.create_vector(&entities);
    let assets = b.create_vector(&assets);
    let instruments = b.create_vector(&instruments);
    let listings = b.create_vector(&listings);
    let markets = b.create_vector(&markets);
    let products = b.create_vector(&products);
    let execution_accesses = b.create_vector(&execution_accesses);
    let market_data_accesses = b.create_vector(&market_data_accesses);
    let provider_health = b.create_vector(&provider_health);
    let option_underlyings = b.create_vector(&option_underlyings);
    let lifecycle_events = b.create_vector(&lifecycle_events);
    let state = fb::ReferenceLatestState::create(
        &mut b,
        &fb::ReferenceLatestStateArgs {
            entities: Some(entities),
            assets: Some(assets),
            instruments: Some(instruments),
            listings: Some(listings),
            markets: Some(markets),
            financial_products: Some(products),
            execution_accesses: Some(execution_accesses),
            market_data_accesses: Some(market_data_accesses),
            provider_health: Some(provider_health),
            option_underlyings: Some(option_underlyings),
            lifecycle_events: Some(lifecycle_events),
        },
    );
    let snapshot_id = b.create_string(&format!("reference-{}", snapshot.generation));
    let resource_id = b.create_string("reference.latest");
    let owner_id = b.create_string(&snapshot.actor_id);
    let workspace_id = b.create_string(&snapshot.workspace_id);
    let launch_id = optional_string(&mut b, snapshot.launch_id.as_deref());
    let instance_id = optional_string(&mut b, snapshot.instance_id.as_deref());
    let metadata = ViewMetadata::create(
        &mut b,
        &ViewMetadataArgs {
            snapshot_id: Some(snapshot_id),
            resource_id: Some(resource_id),
            resource_epoch: 1,
            view_key: Some(resource_id),
            owner_id: Some(owner_id),
            workspace_id: Some(workspace_id),
            launch_id,
            instance_id,
            generation: snapshot.generation,
            as_of_unix_nanos: snapshot
                .lifecycle_events
                .last()
                .map(|value| value.event_time_unix_nanos)
                .unwrap_or_default(),
            published_at_unix_nanos: now_unix_nanos(),
            completeness: ViewCompleteness::COMPLETE,
            applied_revision: Some(snapshot.event_sequence),
            ..Default::default()
        },
    );
    let root = fb::ReferenceLatestView::create(
        &mut b,
        &fb::ReferenceLatestViewArgs {
            metadata: Some(metadata),
            state: Some(state),
        },
    );
    fb::finish_reference_latest_view_buffer(&mut b, root);
    Ok(b.finished_data().to_vec())
}

pub struct MmapReferenceLatestPublisher {
    writer: super::ReferenceViewPublisher,
    producer_incarnation: u64,
}

impl MmapReferenceLatestPublisher {
    pub fn create(
        root: impl AsRef<std::path::Path>,
        actor_id: impl Into<String>,
        slot_capacity: usize,
    ) -> ContractResult<Self> {
        Ok(Self {
            writer: super::ReferenceViewPublisher::create(
                root,
                super::ReferenceViewKey::latest(actor_id),
                slot_capacity,
            )?,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }
    pub fn publish(&mut self, snapshot: &ReferenceLatestSnapshot) -> ContractResult<()> {
        let payload = encode_reference_latest(snapshot)?;
        self.writer.publish(
            SnapshotEnvelopeMetadata {
                resource_epoch: 1,
                producer_incarnation: self.producer_incarnation,
                generation: snapshot.generation,
                applied_event_sequence: snapshot.event_sequence,
                published_at_unix_nanos: now_unix_nanos(),
            },
            &payload,
        )
    }
}

fn entity<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::Entity,
) -> ContractResult<WIPOffset<fb::Entity<'a>>> {
    let entity_id = b.create_string(&value.entity_id);
    let entity_type = b.create_string(&value.entity_type);
    let name = b.create_string(&value.name);
    Ok(fb::Entity::create(
        b,
        &fb::EntityArgs {
            entity_id: Some(entity_id),
            entity_type: Some(entity_type),
            name: Some(name),
            status: lifecycle_status(&value.status)?,
        },
    ))
}

fn asset<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::Asset,
) -> ContractResult<WIPOffset<fb::Asset<'a>>> {
    let asset_id = b.create_string(&value.asset_id);
    let code = b.create_string(&value.code);
    let name = optional_string(b, value.name.as_deref());
    let asset_class = b.create_string(value.asset_class.as_str());
    Ok(fb::Asset::create(
        b,
        &fb::AssetArgs {
            asset_id: Some(asset_id),
            code: Some(code),
            name,
            asset_class: Some(asset_class),
            status: lifecycle_status(&value.status)?,
        },
    ))
}

fn instrument<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::Instrument,
) -> ContractResult<WIPOffset<fb::Instrument<'a>>> {
    let strike = decimal(value.strike.as_deref())?;
    let instrument_id = b.create_string(&value.instrument_id);
    let symbol = b.create_string(&value.symbol);
    let name = optional_string(b, value.name.as_deref());
    let instrument_type = b.create_string(value.instrument_type.as_str());
    let product_family = optional_string(b, value.product_family.as_deref());
    let underlying = optional_string(b, value.underlying_instrument_id.as_deref());
    let option_right = optional_string(b, value.option_right.as_deref());
    let issuer_id = optional_string(b, value.issuer_id.as_deref());
    let share_class = optional_string(b, value.share_class.as_deref());
    let currency = optional_string(b, value.primary_currency_asset_id.as_deref());
    Ok(fb::Instrument::create(
        b,
        &fb::InstrumentArgs {
            instrument_id: Some(instrument_id),
            symbol: Some(symbol),
            name,
            instrument_type: Some(instrument_type),
            product_family,
            underlying_instrument_id: underlying,
            expiry_unix_nanos: value.expiry_unix_nanos.unwrap_or_default(),
            strike: strike.as_ref(),
            option_right,
            issuer_id,
            share_class,
            primary_currency_asset_id: currency,
            status: lifecycle_status(&value.status)?,
        },
    ))
}

fn listing<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::Listing,
) -> ContractResult<WIPOffset<fb::Listing<'a>>> {
    let listing_id = b.create_string(&value.listing_id);
    let instrument_id = b.create_string(&value.instrument_id);
    let exchange_id = b.create_string(&value.exchange_id);
    let exchange_symbol = b.create_string(&value.exchange_symbol);
    Ok(fb::Listing::create(
        b,
        &fb::ListingArgs {
            listing_id: Some(listing_id),
            instrument_id: Some(instrument_id),
            exchange_id: Some(exchange_id),
            exchange_symbol: Some(exchange_symbol),
            status: lifecycle_status(&value.status)?,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos.unwrap_or_default(),
        },
    ))
}

fn market<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::Market,
) -> ContractResult<WIPOffset<fb::Market<'a>>> {
    let price_tick = decimal(value.price_tick.as_deref())?;
    let quantity_tick = decimal(value.quantity_tick.as_deref())?;
    let minimum_quantity = decimal(value.minimum_quantity.as_deref())?;
    let minimum_notional = decimal(value.minimum_notional.as_deref())?;
    let contract_size = decimal(value.contract_size.as_deref())?;
    let market_id = b.create_string(&value.market_id);
    let market_key = b.create_string(&value.market_key);
    let instrument_id = b.create_string(&value.instrument_id);
    let listing_id = b.create_string(&value.listing_id);
    let exchange_id = b.create_string(&value.exchange_id);
    let market_type = b.create_string(value.market_type.as_str());
    let source_symbol = b.create_string(&value.source_symbol);
    let base_asset_id = optional_string(b, value.base_asset_id.as_deref());
    let quote_asset_id = optional_string(b, value.quote_asset_id.as_deref());
    let asset_type = optional_string(b, value.asset_type.as_ref().map(|value| value.as_str()));
    let underlying = optional_string(b, value.underlying_instrument_id.as_deref());
    Ok(fb::Market::create(
        b,
        &fb::MarketArgs {
            market_id: Some(market_id),
            market_key: Some(market_key),
            instrument_id: Some(instrument_id),
            listing_id: Some(listing_id),
            exchange_id: Some(exchange_id),
            market_type: Some(market_type),
            source_symbol: Some(source_symbol),
            base_asset_id,
            quote_asset_id,
            status: lifecycle_status(&value.status)?,
            price_tick: price_tick.as_ref(),
            quantity_tick: quantity_tick.as_ref(),
            price_precision: value.price_precision,
            quantity_precision: value.quantity_precision,
            minimum_quantity: minimum_quantity.as_ref(),
            minimum_notional: minimum_notional.as_ref(),
            contract_size: contract_size.as_ref(),
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos.unwrap_or_default(),
            asset_type,
            underlying_instrument_id: underlying,
        },
    ))
}

fn financial_product<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::FinancialProduct,
) -> ContractResult<WIPOffset<fb::FinancialProduct<'a>>> {
    let min_amount = decimal(value.min_amount.as_deref())?;
    let max_amount = decimal(value.max_amount.as_deref())?;
    let apr = decimal(value.apr.as_deref())?;
    let product_id = b.create_string(&value.product_id);
    let product_type = b.create_string(&value.product_type);
    let name = b.create_string(&value.name);
    let asset_id = b.create_string(&value.asset_id);
    let provider_product_id = b.create_string(&value.provider_product_id);
    let provider_id = optional_string(b, value.provider_id.as_deref());
    let issuer_id = optional_string(b, value.issuer_id.as_deref());
    let currency = optional_string(b, value.currency_asset_id.as_deref());
    Ok(fb::FinancialProduct::create(
        b,
        &fb::FinancialProductArgs {
            product_id: Some(product_id),
            product_type: Some(product_type),
            name: Some(name),
            asset_id: Some(asset_id),
            provider_product_id: Some(provider_product_id),
            provider_id,
            issuer_id,
            currency_asset_id: currency,
            min_amount: min_amount.as_ref(),
            max_amount: max_amount.as_ref(),
            apr: apr.as_ref(),
            lock_period_days: value.lock_period_days,
            maturity_at_unix_nanos: value.maturity_at_unix_nanos.unwrap_or_default(),
            status: lifecycle_status(&value.status)?,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos.unwrap_or_default(),
        },
    ))
}

fn execution_access<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::ExecutionAccess,
) -> ContractResult<WIPOffset<fb::ExecutionAccess<'a>>> {
    let access_id = b.create_string(&value.access_id);
    let routing_mode = optional_string(b, Some(&value.routing_mode));
    let instrument_id = optional_string(b, value.instrument_id.as_deref());
    let listing_id = optional_string(b, value.listing_id.as_deref());
    let market_id = optional_string(b, value.market_id.as_deref());
    let destination_market_id = optional_string(b, value.destination_market_id.as_deref());
    let broker_id = optional_string(b, value.broker_id.as_deref());
    let provider_id = b.create_string(&value.provider_id);
    let product_family = b.create_string(&value.provider_product);
    let provider_symbol = b.create_string(&value.provider_symbol);
    let settlement_asset_id = optional_string(b, value.settlement_asset_id.as_deref());
    Ok(fb::ExecutionAccess::create(
        b,
        &fb::ExecutionAccessArgs {
            access_id: Some(access_id),
            routing_mode,
            instrument_id,
            listing_id,
            market_id,
            destination_market_id,
            broker_id,
            provider_id: Some(provider_id),
            product_family: Some(product_family),
            provider_symbol: Some(provider_symbol),
            settlement_asset_id,
            status: lifecycle_status(&value.status)?,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos.unwrap_or_default(),
        },
    ))
}

fn market_data_access<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: &crate::MarketDataAccess,
) -> ContractResult<WIPOffset<fb::MarketDataAccess<'a>>> {
    let access_id = b.create_string(&value.access_id);
    let market_id = b.create_string(&value.market_id);
    let provider_id = b.create_string(&value.provider_id);
    let product_family = b.create_string(&value.provider_product);
    let provider_symbol = b.create_string(&value.provider_symbol);
    Ok(fb::MarketDataAccess::create(
        b,
        &fb::MarketDataAccessArgs {
            access_id: Some(access_id),
            market_id: Some(market_id),
            provider_id: Some(provider_id),
            product_family: Some(product_family),
            provider_symbol: Some(provider_symbol),
            status: lifecycle_status(&value.status)?,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos.unwrap_or_default(),
        },
    ))
}

fn optional_string<'a>(
    b: &mut FlatBufferBuilder<'a>,
    value: Option<&str>,
) -> Option<WIPOffset<&'a str>> {
    value
        .filter(|value| !value.is_empty())
        .map(|value| b.create_string(value))
}

fn lifecycle_status(value: &str) -> ContractResult<fb::ReferenceLifecycleStatus> {
    Ok(match value.trim().to_ascii_lowercase().as_str() {
        "draft" => fb::ReferenceLifecycleStatus::DRAFT,
        "active" => fb::ReferenceLifecycleStatus::ACTIVE,
        "trading" => fb::ReferenceLifecycleStatus::TRADING,
        "suspended" => fb::ReferenceLifecycleStatus::SUSPENDED,
        "inactive" => fb::ReferenceLifecycleStatus::INACTIVE,
        "retired" | "delisted" => fb::ReferenceLifecycleStatus::RETIRED,
        "expired" => fb::ReferenceLifecycleStatus::EXPIRED,
        other => {
            return Err(ContractError::Invalid(format!(
                "unsupported Reference lifecycle status: {other}"
            )))
        }
    })
}

fn decimal(value: Option<&str>) -> ContractResult<Option<Decimal64>> {
    let Some(value) = value.filter(|value| !value.trim().is_empty()) else {
        return Ok(None);
    };
    let value = value.trim();
    let negative = value.starts_with('-');
    let unsigned = value.trim_start_matches('-');
    let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
    if whole.is_empty()
        || fraction.len() > 18
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(ContractError::Invalid(format!(
            "invalid decimal value: {value}"
        )));
    }
    let magnitude = format!("{whole}{fraction}")
        .parse::<i128>()
        .map_err(|error| ContractError::Invalid(error.to_string()))?;
    let mantissa = i64::try_from(if negative { -magnitude } else { magnitude })
        .map_err(|_| ContractError::Invalid("decimal exceeds i64".into()))?;
    Ok(Some(Decimal64::new(mantissa, fraction.len() as u8)))
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}
