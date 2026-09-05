use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::EventProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, EventMetadata};
use kairos_protocol::generated::kairos::reference::{v_2 as fb, v_3 as fb3};

use crate::catalog::{Asset, Exchange, Instrument, Listing, Market};
use crate::{
    ContractResult, CoverageState, ProviderCatalogMembership, ReferenceCoverage,
    ReferenceCoverageScope, ReferenceFactKind, TradingRules, Venue, VenueKind, VenueListing,
    VenueMarket, VenueRole,
};

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
    pub fn venue_upserted(
        record: &Venue,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue(record, context, occurred_at_unix_nanos, false)
    }

    pub fn venue_updated(
        record: &Venue,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue(record, context, occurred_at_unix_nanos, true)
    }

    pub fn venue_listing_upserted(
        record: &VenueListing,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue_listing(record, context, occurred_at_unix_nanos, false)
    }

    pub fn venue_listing_updated(
        record: &VenueListing,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue_listing(record, context, occurred_at_unix_nanos, true)
    }

    pub fn venue_market_upserted(
        record: &VenueMarket,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue_market(record, context, occurred_at_unix_nanos, false)
    }

    pub fn venue_market_updated(
        record: &VenueMarket,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_venue_market(record, context, occurred_at_unix_nanos, true)
    }

    pub fn provider_catalog_membership_upserted(
        record: &ProviderCatalogMembership,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_provider_catalog_membership(record, context, occurred_at_unix_nanos, false)
    }

    pub fn provider_catalog_membership_updated(
        record: &ProviderCatalogMembership,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_provider_catalog_membership(record, context, occurred_at_unix_nanos, true)
    }

    pub fn coverage_state_changed(
        record: &ReferenceCoverage,
        previous_state: CoverageState,
        context: &EncodeContext,
        occurred_at_unix_nanos: u64,
    ) -> ContractResult<Vec<u8>> {
        encode_coverage_state_changed(record, previous_state, context, occurred_at_unix_nanos)
    }

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

fn encode_venue(
    record: &Venue,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let venue_id = builder.create_string(record.venue_id.as_str());
    let name = builder.create_string(&record.name);
    let roles = record
        .roles
        .iter()
        .copied()
        .map(venue_role)
        .collect::<Vec<_>>();
    let roles = builder.create_vector(&roles);
    let mic = optional_string(&mut builder, record.mic.as_deref());
    let operating_mic = optional_string(&mut builder, record.operating_mic.as_deref());
    let parent_venue_id = optional_string(&mut builder, record.parent_venue_id.as_deref());
    let jurisdiction = optional_string(&mut builder, record.jurisdiction.as_deref());
    let venue = fb3::Venue::create(
        &mut builder,
        &fb3::VenueArgs {
            venue_id: Some(venue_id),
            name: Some(name),
            venue_kind: venue_kind(record.venue_kind),
            roles: Some(roles),
            mic,
            operating_mic,
            parent_venue_id,
            jurisdiction,
            status: status_v3(record.status),
        },
    );
    if updated {
        let root = fb3::VenueUpdated::create(
            &mut builder,
            &fb3::VenueUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                venue: Some(venue),
            },
        );
        fb3::finish_venue_updated_buffer(&mut builder, root);
    } else {
        let root = fb3::VenueUpserted::create(
            &mut builder,
            &fb3::VenueUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                venue: Some(venue),
            },
        );
        fb3::finish_venue_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_venue_listing(
    record: &VenueListing,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let listing_id = builder.create_string(record.listing_id.as_str());
    let instrument_id = builder.create_string(record.instrument_id.as_str());
    let listing_venue_id = builder.create_string(record.listing_venue_id.as_str());
    let market_segment_id = optional_string(&mut builder, record.market_segment_id.as_deref());
    let listing_symbol = builder.create_string(record.listing_symbol.as_str());
    let listing = fb3::Listing::create(
        &mut builder,
        &fb3::ListingArgs {
            listing_id: Some(listing_id),
            instrument_id: Some(instrument_id),
            listing_venue_id: Some(listing_venue_id),
            market_segment_id,
            listing_symbol: Some(listing_symbol),
            listing_role: listing_role(record.listing_role),
            status: status_v3(record.status),
            effective_from_unix_nanos: record.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default().get(),
        },
    );
    if updated {
        let root = fb3::ListingUpdated::create(
            &mut builder,
            &fb3::ListingUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                listing: Some(listing),
            },
        );
        fb3::finish_listing_updated_buffer(&mut builder, root);
    } else {
        let root = fb3::ListingUpserted::create(
            &mut builder,
            &fb3::ListingUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                listing: Some(listing),
            },
        );
        fb3::finish_listing_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_venue_market(
    record: &VenueMarket,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let market_id = builder.create_string(record.market_id.as_str());
    let instrument_id = builder.create_string(record.instrument_id.as_str());
    let execution_venue_id = builder.create_string(record.execution_venue_id.as_str());
    let origin_listing_id = optional_string(&mut builder, record.origin_listing_id.as_deref());
    let market_segment_id = optional_string(&mut builder, record.market_segment_id.as_deref());
    let venue_symbol = optional_string(&mut builder, record.venue_symbol.as_deref());
    let trading_calendar_id = optional_string(&mut builder, record.trading_calendar_id.as_deref());
    let session_strings = record
        .trading_session_ids
        .iter()
        .map(|value| builder.create_string(value.as_str()))
        .collect::<Vec<_>>();
    let trading_session_ids = builder.create_vector(&session_strings);
    let base_asset_id = optional_string(&mut builder, record.base_asset_id.as_deref());
    let quote_asset_id = optional_string(&mut builder, record.quote_asset_id.as_deref());
    let trading_rules = encode_trading_rules(&mut builder, &record.trading_rules);
    let market = fb3::Market::create(
        &mut builder,
        &fb3::MarketArgs {
            market_id: Some(market_id),
            instrument_id: Some(instrument_id),
            execution_venue_id: Some(execution_venue_id),
            origin_listing_id,
            market_segment_id,
            venue_symbol,
            trading_calendar_id,
            trading_session_ids: Some(trading_session_ids),
            base_asset_id,
            quote_asset_id,
            status: status_v3(record.status),
            trading_rules: Some(trading_rules),
            effective_from_unix_nanos: record.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default().get(),
        },
    );
    if updated {
        let root = fb3::MarketUpdated::create(
            &mut builder,
            &fb3::MarketUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                market: Some(market),
            },
        );
        fb3::finish_market_updated_buffer(&mut builder, root);
    } else {
        let root = fb3::MarketUpserted::create(
            &mut builder,
            &fb3::MarketUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                market: Some(market),
            },
        );
        fb3::finish_market_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_provider_catalog_membership(
    record: &ProviderCatalogMembership,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
    updated: bool,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let source_id = builder.create_string(record.source_id.as_str());
    let instrument_id = builder.create_string(record.instrument_id.as_str());
    let provider_symbol = optional_string(&mut builder, record.provider_symbol.as_deref());
    let provider_product = optional_string(&mut builder, record.provider_product.as_deref());
    let membership = fb3::ProviderCatalogMembership::create(
        &mut builder,
        &fb3::ProviderCatalogMembershipArgs {
            source_id: Some(source_id),
            instrument_id: Some(instrument_id),
            provider_symbol,
            provider_product,
            status: status_v3(record.status),
            effective_from_unix_nanos: record.effective_from_unix_nanos.get(),
            effective_to_unix_nanos: record.effective_to_unix_nanos.unwrap_or_default().get(),
        },
    );
    if updated {
        let root = fb3::ProviderCatalogMembershipUpdated::create(
            &mut builder,
            &fb3::ProviderCatalogMembershipUpdatedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                membership: Some(membership),
            },
        );
        fb3::finish_provider_catalog_membership_updated_buffer(&mut builder, root);
    } else {
        let root = fb3::ProviderCatalogMembershipUpserted::create(
            &mut builder,
            &fb3::ProviderCatalogMembershipUpsertedArgs {
                metadata: Some(metadata),
                catalog_revision: context.catalog_revision.get(),
                membership: Some(membership),
            },
        );
        fb3::finish_provider_catalog_membership_upserted_buffer(&mut builder, root);
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_coverage_state_changed(
    record: &ReferenceCoverage,
    previous_state: CoverageState,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> ContractResult<Vec<u8>> {
    let mut builder = FlatBufferBuilder::new();
    let metadata = event_metadata(&mut builder, context, occurred_at_unix_nanos);
    let coverage_id = builder.create_string(record.coverage_id.as_str());
    let source_id = builder.create_string(record.source_id.as_str());
    let fact_kinds = record
        .fact_kinds
        .iter()
        .copied()
        .map(fact_kind)
        .collect::<Vec<_>>();
    let fact_kinds = builder.create_vector(&fact_kinds);
    let (scope_kind, scope_binding, scope_values, scope_instrument_kind) =
        coverage_scope_parts(record);
    let scope_kind = builder.create_string(scope_kind);
    let scope_binding = optional_string(&mut builder, scope_binding);
    let scope_values = scope_values
        .iter()
        .map(|value| builder.create_string(value))
        .collect::<Vec<_>>();
    let scope_ids = builder.create_vector(&scope_values);
    let scope_instrument_kind = optional_string(&mut builder, scope_instrument_kind);
    let coverage = fb3::ReferenceCoverage::create(
        &mut builder,
        &fb3::ReferenceCoverageArgs {
            coverage_id: Some(coverage_id),
            source_id: Some(source_id),
            fact_kinds: Some(fact_kinds),
            scope_kind: Some(scope_kind),
            scope_binding,
            scope_ids: Some(scope_ids),
            scope_instrument_kind,
            completeness: coverage_completeness(record.completeness),
            state: coverage_state(record.state),
            generation: record.generation.unwrap_or_default().get(),
            event_sequence: record.event_sequence.unwrap_or_default().get(),
            last_attempt_unix_nanos: record.last_attempt_unix_nanos.unwrap_or_default().get(),
            last_success_unix_nanos: record.last_success_unix_nanos.unwrap_or_default().get(),
            stale_after_unix_nanos: record.stale_after_unix_nanos.unwrap_or_default().get(),
            has_last_known_good: record.has_last_known_good,
        },
    );
    let root = fb3::CoverageStateChanged::create(
        &mut builder,
        &fb3::CoverageStateChangedArgs {
            metadata: Some(metadata),
            catalog_revision: context.catalog_revision.get(),
            coverage: Some(coverage),
            previous_state: coverage_state(previous_state),
        },
    );
    fb3::finish_coverage_state_changed_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_trading_rules<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    rules: &TradingRules,
) -> WIPOffset<fb3::TradingRules<'a>> {
    let price_tick = rules
        .price_tick
        .map(|value| decimal(value.mantissa(), value.scale()));
    let quantity_tick = rules
        .quantity_tick
        .map(|value| decimal(value.mantissa(), value.scale()));
    let minimum_quantity = rules
        .minimum_quantity
        .map(|value| decimal(value.mantissa(), value.scale()));
    let minimum_notional = rules
        .minimum_notional
        .map(|value| decimal(value.mantissa(), value.scale()));
    let contract_size = rules
        .contract_size
        .map(|value| decimal(value.mantissa(), value.scale()));
    fb3::TradingRules::create(
        builder,
        &fb3::TradingRulesArgs {
            price_tick: price_tick.as_ref(),
            quantity_tick: quantity_tick.as_ref(),
            price_precision: rules.price_precision,
            quantity_precision: rules.quantity_precision,
            minimum_quantity: minimum_quantity.as_ref(),
            minimum_notional: minimum_notional.as_ref(),
            contract_size: contract_size.as_ref(),
        },
    )
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
    let settlement_asset_id = optional_string(&mut builder, record.settlement_asset_id.as_deref());
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
        settlement_asset_id,
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

fn status_v3(
    value: kairos_primitives::reference::ReferenceStatus,
) -> fb3::ReferenceLifecycleStatus {
    use kairos_primitives::reference::ReferenceStatus as Status;
    match value {
        Status::Draft => fb3::ReferenceLifecycleStatus::DRAFT,
        Status::Active => fb3::ReferenceLifecycleStatus::ACTIVE,
        Status::Trading => fb3::ReferenceLifecycleStatus::TRADING,
        Status::Inactive | Status::Retired => fb3::ReferenceLifecycleStatus::INACTIVE,
        Status::Suspended => fb3::ReferenceLifecycleStatus::SUSPENDED,
        Status::Delisted => fb3::ReferenceLifecycleStatus::DELISTED,
        Status::Expired => fb3::ReferenceLifecycleStatus::EXPIRED,
        Status::Unknown => fb3::ReferenceLifecycleStatus::UNKNOWN,
    }
}

fn venue_kind(value: VenueKind) -> fb3::VenueKind {
    match value {
        VenueKind::RegulatedExchange => fb3::VenueKind::REGULATED_EXCHANGE,
        VenueKind::RegulatedMarket => fb3::VenueKind::REGULATED_MARKET,
        VenueKind::TradingPlatform => fb3::VenueKind::TRADING_PLATFORM,
        VenueKind::Ats => fb3::VenueKind::ATS,
        VenueKind::Pts => fb3::VenueKind::PTS,
        VenueKind::OtcFacility => fb3::VenueKind::OTC_FACILITY,
        VenueKind::Dealer => fb3::VenueKind::DEALER,
        VenueKind::TradeReportingFacility => fb3::VenueKind::TRADE_REPORTING_FACILITY,
        VenueKind::Unknown => fb3::VenueKind::UNKNOWN,
    }
}

fn venue_role(value: VenueRole) -> fb3::VenueRole {
    match value {
        VenueRole::Listing => fb3::VenueRole::LISTING,
        VenueRole::Execution => fb3::VenueRole::EXECUTION,
        VenueRole::Reporting => fb3::VenueRole::REPORTING,
    }
}

fn listing_role(value: crate::ListingRole) -> fb3::ListingRole {
    match value {
        crate::ListingRole::Primary => fb3::ListingRole::PRIMARY,
        crate::ListingRole::Secondary => fb3::ListingRole::SECONDARY,
        crate::ListingRole::CrossListing => fb3::ListingRole::CROSS_LISTING,
        crate::ListingRole::AdmissionWithoutPrimaryDesignation => {
            fb3::ListingRole::ADMISSION_WITHOUT_PRIMARY_DESIGNATION
        },
        crate::ListingRole::Unknown => fb3::ListingRole::UNKNOWN,
    }
}

fn coverage_completeness(value: crate::CoverageCompleteness) -> fb3::CoverageCompleteness {
    match value {
        crate::CoverageCompleteness::Unknown => fb3::CoverageCompleteness::UNKNOWN,
        crate::CoverageCompleteness::Partial => fb3::CoverageCompleteness::PARTIAL,
        crate::CoverageCompleteness::CompleteForDeclaredScope => {
            fb3::CoverageCompleteness::COMPLETE_FOR_DECLARED_SCOPE
        },
    }
}

fn coverage_state(value: CoverageState) -> fb3::CoverageState {
    match value {
        CoverageState::NotConfigured => fb3::CoverageState::NOT_CONFIGURED,
        CoverageState::Waiting => fb3::CoverageState::WAITING,
        CoverageState::Scanning => fb3::CoverageState::SCANNING,
        CoverageState::Promoting => fb3::CoverageState::PROMOTING,
        CoverageState::Usable => fb3::CoverageState::USABLE,
        CoverageState::Stale => fb3::CoverageState::STALE,
        CoverageState::RetryWaiting => fb3::CoverageState::RETRY_WAITING,
        CoverageState::Paused => fb3::CoverageState::PAUSED,
        CoverageState::Unavailable => fb3::CoverageState::UNAVAILABLE,
    }
}

fn fact_kind(value: ReferenceFactKind) -> fb3::ReferenceFactKind {
    match value {
        ReferenceFactKind::Venue => fb3::ReferenceFactKind::VENUE,
        ReferenceFactKind::Asset => fb3::ReferenceFactKind::ASSET,
        ReferenceFactKind::Instrument => fb3::ReferenceFactKind::INSTRUMENT,
        ReferenceFactKind::Listing => fb3::ReferenceFactKind::LISTING,
        ReferenceFactKind::Market => fb3::ReferenceFactKind::MARKET,
        ReferenceFactKind::ProviderCatalogMembership => {
            fb3::ReferenceFactKind::PROVIDER_CATALOG_MEMBERSHIP
        },
        ReferenceFactKind::VenueIdentifierMapping => {
            fb3::ReferenceFactKind::VENUE_IDENTIFIER_MAPPING
        },
        ReferenceFactKind::TradingRules => fb3::ReferenceFactKind::TRADING_RULES,
    }
}

fn coverage_scope_parts<'a>(
    coverage: &'a ReferenceCoverage,
) -> (
    &'static str,
    Option<&'static str>,
    Vec<&'a str>,
    Option<&'static str>,
) {
    match &coverage.scope {
        ReferenceCoverageScope::ProviderCatalog { binding } => (
            "provider_catalog",
            Some(binding.source_id()),
            Vec::new(),
            None,
        ),
        ReferenceCoverageScope::VenueListings {
            venue_ids,
            instrument_kind,
        } => (
            "venue_listings",
            None,
            venue_ids.iter().map(|value| value.as_str()).collect(),
            Some(instrument_kind.as_str()),
        ),
        ReferenceCoverageScope::VenueMarkets {
            venue_ids,
            instrument_kind,
        } => (
            "venue_markets",
            None,
            venue_ids.iter().map(|value| value.as_str()).collect(),
            Some(instrument_kind.as_str()),
        ),
        ReferenceCoverageScope::UnderlyingOptions {
            underlying_instrument_ids,
        } => (
            "underlying_options",
            None,
            underlying_instrument_ids
                .iter()
                .map(|value| value.as_str())
                .collect(),
            Some("option"),
        ),
    }
}

fn decimal(mantissa: i64, scale: u8) -> Decimal64 {
    Decimal64::new(mantissa, scale)
}

#[cfg(test)]
mod settlement_tests {
    #[test]
    fn instrument_events_preserve_known_and_unknown_settlement() {
        let context = super::EncodeContext::event(
            "reference-test",
            1,
            kairos_primitives::runtime::InstanceIdentity::unscoped("workspace:test").unwrap(),
            1,
            "settlement-event",
            1,
        )
        .unwrap();
        for settlement in [None, Some("asset:crypto:BTC")] {
            let instrument = crate::Instrument {
                instrument_id: kairos_primitives::reference::InstrumentId::new(
                    "instrument:perpetual:test",
                )
                .unwrap(),
                symbol: kairos_primitives::reference::Symbol::new("TEST").unwrap(),
                instrument_type: kairos_primitives::reference::InstrumentKind::Perpetual,
                settlement_asset_id: settlement
                    .map(|value| kairos_primitives::reference::AssetId::new(value).unwrap()),
                ..Default::default()
            };
            let added =
                super::ReferenceEncoder::instrument_upserted(&instrument, &context, 1).unwrap();
            let crate::ReferenceEvent::InstrumentUpserted(event) =
                crate::decode_event(&added).unwrap()
            else {
                panic!("expected instrument upsert");
            };
            assert_eq!(event.instrument().settlement_asset_id(), settlement);
            let updated =
                super::ReferenceEncoder::instrument_updated(&instrument, &context, 1).unwrap();
            let crate::ReferenceEvent::InstrumentUpdated(event) =
                crate::decode_event(&updated).unwrap()
            else {
                panic!("expected instrument update");
            };
            assert_eq!(event.instrument().settlement_asset_id(), settlement);
        }
    }
}
