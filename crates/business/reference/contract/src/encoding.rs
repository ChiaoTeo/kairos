//! FlatBuffers encoding for reference read models.

use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs, SnapshotHeader, SnapshotHeaderArgs,
};
use kairos_protocol::generated::kairos::reference::v_1::{
    finish_catalog_snapshot_buffer, finish_markets_snapshot_buffer,
    finish_reference_changed_buffer, finish_reference_collections_snapshot_buffer,
    Asset as FbAsset, AssetArgs as FbAssetArgs, Catalog as FbCatalog, CatalogArgs as FbCatalogArgs,
    CatalogSnapshot as FbCatalogSnapshot, CatalogSnapshotArgs as FbCatalogSnapshotArgs,
    Entity as FbEntity, EntityArgs as FbEntityArgs, ExecutionAccess as FbExecutionAccess,
    ExecutionAccessArgs as FbExecutionAccessArgs, FinancialProduct as FbFinancialProduct,
    FinancialProductArgs as FbFinancialProductArgs, Instrument as FbInstrument,
    InstrumentArgs as FbInstrumentArgs, LifecycleEvent as FbLifecycleEvent,
    LifecycleEventArgs as FbLifecycleEventArgs, Listing as FbListing, ListingArgs as FbListingArgs,
    Market as FbMarket, MarketArgs as FbMarketArgs, Markets as FbMarkets,
    MarketsArgs as FbMarketsArgs, MarketsSnapshot as FbMarketsSnapshot,
    MarketsSnapshotArgs as FbMarketsSnapshotArgs, ReferenceChanged as FbReferenceChanged,
    ReferenceChangedArgs as FbReferenceChangedArgs, ReferenceCollections as FbReferenceCollections,
    ReferenceCollectionsArgs as FbReferenceCollectionsArgs,
    ReferenceCollectionsSnapshot as FbReferenceCollectionsSnapshot,
    ReferenceCollectionsSnapshotArgs as FbReferenceCollectionsSnapshotArgs,
};
use kairos_protocol::InstanceIdentity;

use crate::error::{ContractError, ContractResult};
use crate::model::{LifecycleEvent, ReferenceCatalog};
use std::time::{SystemTime, UNIX_EPOCH};

pub struct FlatbuffersSnapshotEncoder {
    pub actor_id: String,
    pub event_stream_id: String,
    pub identity: InstanceIdentity,
}

impl FlatbuffersSnapshotEncoder {
    pub fn new(actor_id: impl Into<String>, event_stream_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity: InstanceIdentity::default(),
        }
    }

    pub fn with_identity(
        actor_id: impl Into<String>,
        event_stream_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
            identity,
        }
    }

    pub fn encode_catalog(&self, catalog: &ReferenceCatalog) -> ContractResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let entity_offsets: Vec<_> = catalog
            .entities
            .values()
            .map(|v| {
                let entity_id = builder.create_string(&v.entity_id);
                let entity_type = builder.create_string(&v.entity_type);
                let name = builder.create_string(&v.name);
                let status = builder.create_string(&v.status);
                FbEntity::create(
                    &mut builder,
                    &FbEntityArgs {
                        entity_id: Some(entity_id),
                        entity_type: Some(entity_type),
                        name: Some(name),
                        status: Some(status),
                    },
                )
            })
            .collect();
        let asset_offsets: Vec<_> = catalog
            .assets
            .values()
            .map(|v| {
                let asset_id = builder.create_string(&v.asset_id);
                let code = builder.create_string(&v.code);
                let name = v.name.as_ref().map(|x| builder.create_string(x));
                let class = builder.create_string(&v.asset_class);
                let status = builder.create_string(&v.status);
                FbAsset::create(
                    &mut builder,
                    &FbAssetArgs {
                        asset_id: Some(asset_id),
                        code: Some(code),
                        name,
                        asset_class: Some(class),
                        status: Some(status),
                    },
                )
            })
            .collect();
        let financial_product_offsets: Vec<_> = catalog
            .financial_products
            .values()
            .map(|v| {
                let product_id = builder.create_string(&v.product_id);
                let product_type = builder.create_string(&v.product_type);
                let name = builder.create_string(&v.name);
                let asset_id = builder.create_string(&v.asset_id);
                let provider_product_id = builder.create_string(&v.provider_product_id);
                let provider_id = v.provider_id.as_ref().map(|x| builder.create_string(x));
                let issuer_id = v.issuer_id.as_ref().map(|x| builder.create_string(x));
                let currency_asset_id = v
                    .currency_asset_id
                    .as_ref()
                    .map(|x| builder.create_string(x));
                let min_amount = decimal64(v.min_amount.as_deref());
                let max_amount = decimal64(v.max_amount.as_deref());
                let apr = decimal64(v.apr.as_deref());
                let status = builder.create_string(&v.status);
                FbFinancialProduct::create(
                    &mut builder,
                    &FbFinancialProductArgs {
                        product_id: Some(product_id),
                        product_type: Some(product_type),
                        name: Some(name),
                        asset_id: Some(asset_id),
                        provider_product_id: Some(provider_product_id),
                        provider_id,
                        issuer_id,
                        currency_asset_id,
                        min_amount: min_amount.as_ref(),
                        max_amount: max_amount.as_ref(),
                        apr: apr.as_ref(),
                        lock_period_days: v.lock_period_days,
                        maturity_at_unix_nanos: v.maturity_at_unix_nanos.unwrap_or_default(),
                        status: Some(status),
                        effective_from_unix_nanos: v.effective_from_unix_nanos,
                        effective_to_unix_nanos: v.effective_to_unix_nanos.unwrap_or_default(),
                    },
                )
            })
            .collect();
        let execution_access_offsets: Vec<_> = catalog
            .execution_accesses
            .values()
            .map(|v| {
                let access_id = builder.create_string(&v.access_id);
                let market_id = builder.create_string(&v.market_id);
                let provider_id = builder.create_string(&v.provider_id);
                let product_family = builder.create_string(&v.product_family);
                let provider_symbol = builder.create_string(&v.provider_symbol);
                let settlement_asset_id = v
                    .settlement_asset_id
                    .as_ref()
                    .map(|x| builder.create_string(x));
                let status = builder.create_string(&v.status);
                FbExecutionAccess::create(
                    &mut builder,
                    &FbExecutionAccessArgs {
                        access_id: Some(access_id),
                        market_id: Some(market_id),
                        provider_id: Some(provider_id),
                        product_family: Some(product_family),
                        provider_symbol: Some(provider_symbol),
                        settlement_asset_id,
                        status: Some(status),
                        effective_from_unix_nanos: v.effective_from_unix_nanos,
                        effective_to_unix_nanos: v.effective_to_unix_nanos.unwrap_or_default(),
                    },
                )
            })
            .collect();
        let instrument_offsets: Vec<_> = catalog
            .instruments
            .values()
            .map(|v| {
                let instrument_id = builder.create_string(&v.instrument_id);
                let symbol = builder.create_string(&v.symbol);
                let name = v.name.as_ref().map(|x| builder.create_string(x));
                let kind = builder.create_string(&v.instrument_type);
                let family = v.product_family.as_ref().map(|x| builder.create_string(x));
                let underlying = v
                    .underlying_instrument_id
                    .as_ref()
                    .map(|x| builder.create_string(x));
                let strike = decimal64(v.strike.as_deref());
                let option_right = v.option_right.as_ref().map(|x| builder.create_string(x));
                let issuer_id = v.issuer_id.as_ref().map(|x| builder.create_string(x));
                let share_class = v.share_class.as_ref().map(|x| builder.create_string(x));
                let primary_currency_asset_id = v
                    .primary_currency_asset_id
                    .as_ref()
                    .map(|x| builder.create_string(x));
                let status = builder.create_string(&v.status);
                FbInstrument::create(
                    &mut builder,
                    &FbInstrumentArgs {
                        instrument_id: Some(instrument_id),
                        symbol: Some(symbol),
                        name,
                        instrument_type: Some(kind),
                        product_family: family,
                        underlying_instrument_id: underlying,
                        expiry_unix_nanos: v.expiry_unix_nanos.unwrap_or_default(),
                        strike: strike.as_ref(),
                        option_right,
                        issuer_id,
                        share_class,
                        primary_currency_asset_id,
                        status: Some(status),
                        ..Default::default()
                    },
                )
            })
            .collect();
        let listing_offsets: Vec<_> = catalog
            .listings
            .values()
            .map(|v| {
                let listing_id = builder.create_string(&v.listing_id);
                let instrument_id = builder.create_string(&v.instrument_id);
                let exchange_id = builder.create_string(&v.exchange_id);
                let symbol = builder.create_string(&v.exchange_symbol);
                let status = builder.create_string(&v.status);
                FbListing::create(
                    &mut builder,
                    &FbListingArgs {
                        listing_id: Some(listing_id),
                        instrument_id: Some(instrument_id),
                        exchange_id: Some(exchange_id),
                        exchange_symbol: Some(symbol),
                        status: Some(status),
                        effective_from_unix_nanos: v.effective_from_unix_nanos,
                        effective_to_unix_nanos: v.effective_to_unix_nanos.unwrap_or_default(),
                    },
                )
            })
            .collect();
        let market_offsets = self.market_offsets(&mut builder, catalog);
        let entities = builder.create_vector(&entity_offsets);
        let assets = builder.create_vector(&asset_offsets);
        let financial_products = builder.create_vector(&financial_product_offsets);
        let instruments = builder.create_vector(&instrument_offsets);
        let listings = builder.create_vector(&listing_offsets);
        let markets = builder.create_vector(&market_offsets);
        let execution_accesses = builder.create_vector(&execution_access_offsets);
        let payload = FbCatalog::create(
            &mut builder,
            &FbCatalogArgs {
                entity_count: catalog.entities.len() as u64,
                asset_count: catalog.assets.len() as u64,
                instrument_count: catalog.instruments.len() as u64,
                listing_count: catalog.listings.len() as u64,
                market_count: catalog.markets.len() as u64,
                financial_product_count: catalog.financial_products.len() as u64,
                execution_access_count: catalog.execution_accesses.len() as u64,
                active_market_count: catalog.active_market_count() as u64,
                lifecycle_event_count: catalog.lifecycle_events.len() as u64,
                entities: Some(entities),
                assets: Some(assets),
                financial_products: Some(financial_products),
                execution_accesses: Some(execution_accesses),
                instruments: Some(instruments),
                listings: Some(listings),
                markets: Some(markets),
            },
        );
        let header = self.header(&mut builder, "reference.catalog", catalog);
        let root = FbCatalogSnapshot::create(
            &mut builder,
            &FbCatalogSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_catalog_snapshot_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }

    pub fn encode_markets(&self, catalog: &ReferenceCatalog) -> ContractResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let market_offsets = self.market_offsets(&mut builder, catalog);
        let markets = builder.create_vector(&market_offsets);
        let payload = FbMarkets::create(
            &mut builder,
            &FbMarketsArgs {
                total_count: catalog.markets.len() as u64,
                active_count: catalog.active_market_count() as u64,
                markets: Some(markets),
            },
        );
        let header = self.header(&mut builder, "reference.markets", catalog);
        let root = FbMarketsSnapshot::create(
            &mut builder,
            &FbMarketsSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_markets_snapshot_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }

    /// Encode one normalized Reference collection as an independently
    /// addressable current-state snapshot. Only the selected collection is
    /// populated; the shared schema keeps one stable root type for clients.
    pub fn encode_collection(
        &self,
        catalog: &ReferenceCatalog,
        collection: &str,
    ) -> ContractResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let entities = if collection == "entities" {
            let offsets = catalog
                .entities
                .values()
                .map(|value| {
                    let entity_id = builder.create_string(&value.entity_id);
                    let entity_type = builder.create_string(&value.entity_type);
                    let name = builder.create_string(&value.name);
                    let status = builder.create_string(&value.status);
                    FbEntity::create(
                        &mut builder,
                        &FbEntityArgs {
                            entity_id: Some(entity_id),
                            entity_type: Some(entity_type),
                            name: Some(name),
                            status: Some(status),
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let assets = if collection == "assets" {
            let offsets = catalog
                .assets
                .values()
                .map(|value| {
                    let asset_id = builder.create_string(&value.asset_id);
                    let code = builder.create_string(&value.code);
                    let name = value.name.as_ref().map(|item| builder.create_string(item));
                    let class = builder.create_string(&value.asset_class);
                    let status = builder.create_string(&value.status);
                    FbAsset::create(
                        &mut builder,
                        &FbAssetArgs {
                            asset_id: Some(asset_id),
                            code: Some(code),
                            name,
                            asset_class: Some(class),
                            status: Some(status),
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let instruments = if collection == "instruments" {
            let offsets = catalog
                .instruments
                .values()
                .map(|value| {
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let symbol = builder.create_string(&value.symbol);
                    let name = value.name.as_ref().map(|item| builder.create_string(item));
                    let kind = builder.create_string(&value.instrument_type);
                    let family = value
                        .product_family
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let underlying = value
                        .underlying_instrument_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let strike = decimal64(value.strike.as_deref());
                    let option_right = value
                        .option_right
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let issuer_id = value
                        .issuer_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let share_class = value
                        .share_class
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let primary_currency_asset_id = value
                        .primary_currency_asset_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let status = builder.create_string(&value.status);
                    FbInstrument::create(
                        &mut builder,
                        &FbInstrumentArgs {
                            instrument_id: Some(instrument_id),
                            symbol: Some(symbol),
                            name,
                            instrument_type: Some(kind),
                            product_family: family,
                            underlying_instrument_id: underlying,
                            expiry_unix_nanos: value.expiry_unix_nanos.unwrap_or_default(),
                            strike: strike.as_ref(),
                            option_right,
                            issuer_id,
                            share_class,
                            primary_currency_asset_id,
                            status: Some(status),
                            ..Default::default()
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let listings = if collection == "listings" {
            let offsets = catalog
                .listings
                .values()
                .map(|value| {
                    let listing_id = builder.create_string(&value.listing_id);
                    let instrument_id = builder.create_string(&value.instrument_id);
                    let exchange_id = builder.create_string(&value.exchange_id);
                    let symbol = builder.create_string(&value.exchange_symbol);
                    let status = builder.create_string(&value.status);
                    FbListing::create(
                        &mut builder,
                        &FbListingArgs {
                            listing_id: Some(listing_id),
                            instrument_id: Some(instrument_id),
                            exchange_id: Some(exchange_id),
                            exchange_symbol: Some(symbol),
                            status: Some(status),
                            effective_from_unix_nanos: value.effective_from_unix_nanos,
                            effective_to_unix_nanos: value
                                .effective_to_unix_nanos
                                .unwrap_or_default(),
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let markets = if collection == "markets" {
            let offsets = self.market_offsets(&mut builder, catalog);
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let financial_products = if collection == "financial_products" {
            let offsets = catalog
                .financial_products
                .values()
                .map(|value| {
                    let product_id = builder.create_string(&value.product_id);
                    let product_type = builder.create_string(&value.product_type);
                    let name = builder.create_string(&value.name);
                    let asset_id = builder.create_string(&value.asset_id);
                    let provider_product_id = builder.create_string(&value.provider_product_id);
                    let provider_id = value
                        .provider_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let issuer_id = value
                        .issuer_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let currency_asset_id = value
                        .currency_asset_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let min_amount = decimal64(value.min_amount.as_deref());
                    let max_amount = decimal64(value.max_amount.as_deref());
                    let apr = decimal64(value.apr.as_deref());
                    let status = builder.create_string(&value.status);
                    FbFinancialProduct::create(
                        &mut builder,
                        &FbFinancialProductArgs {
                            product_id: Some(product_id),
                            product_type: Some(product_type),
                            name: Some(name),
                            asset_id: Some(asset_id),
                            provider_product_id: Some(provider_product_id),
                            provider_id,
                            issuer_id,
                            currency_asset_id,
                            min_amount: min_amount.as_ref(),
                            max_amount: max_amount.as_ref(),
                            apr: apr.as_ref(),
                            lock_period_days: value.lock_period_days,
                            maturity_at_unix_nanos: value
                                .maturity_at_unix_nanos
                                .unwrap_or_default(),
                            status: Some(status),
                            effective_from_unix_nanos: value.effective_from_unix_nanos,
                            effective_to_unix_nanos: value
                                .effective_to_unix_nanos
                                .unwrap_or_default(),
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let execution_accesses = if collection == "execution_accesses" {
            let offsets = catalog
                .execution_accesses
                .values()
                .map(|value| {
                    let access_id = builder.create_string(&value.access_id);
                    let market_id = builder.create_string(&value.market_id);
                    let provider_id = builder.create_string(&value.provider_id);
                    let product_family = builder.create_string(&value.product_family);
                    let provider_symbol = builder.create_string(&value.provider_symbol);
                    let settlement_asset_id = value
                        .settlement_asset_id
                        .as_ref()
                        .map(|item| builder.create_string(item));
                    let status = builder.create_string(&value.status);
                    FbExecutionAccess::create(
                        &mut builder,
                        &FbExecutionAccessArgs {
                            access_id: Some(access_id),
                            market_id: Some(market_id),
                            provider_id: Some(provider_id),
                            product_family: Some(product_family),
                            provider_symbol: Some(provider_symbol),
                            settlement_asset_id,
                            status: Some(status),
                            effective_from_unix_nanos: value.effective_from_unix_nanos,
                            effective_to_unix_nanos: value
                                .effective_to_unix_nanos
                                .unwrap_or_default(),
                        },
                    )
                })
                .collect::<Vec<_>>();
            Some(builder.create_vector(&offsets))
        } else {
            None
        };
        let view_key = match collection {
            "entities" | "assets" | "instruments" | "listings" | "markets"
            | "financial_products" | "execution_accesses" => format!("reference.{collection}"),
            _ => {
                return Err(ContractError::Invalid(format!(
                    "unknown collection: {collection}"
                )))
            }
        };
        let payload = FbReferenceCollections::create(
            &mut builder,
            &FbReferenceCollectionsArgs {
                entities,
                assets,
                instruments,
                listings,
                markets,
                financial_products,
                execution_accesses,
            },
        );
        let header = self.header(&mut builder, &view_key, catalog);
        let root = FbReferenceCollectionsSnapshot::create(
            &mut builder,
            &FbReferenceCollectionsSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_reference_collections_snapshot_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }

    pub fn encode_change(
        &self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ContractResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let message_id = builder.create_string(&format!("reference:{}", catalog.event_sequence));
        let stream_id = builder.create_string(&self.event_stream_id);
        let producer_id = builder.create_string(&self.actor_id);
        let snapshot_id = builder.create_string(&format!("reference:{}", catalog.generation));
        let event_offsets: Vec<_> = events
            .iter()
            .map(|event| {
                let event_id = builder.create_string(&event.event_id);
                let event_type = builder.create_string(&event.event_type);
                let market_id = event
                    .market_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let instrument_id = event
                    .instrument_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let listing_id = event
                    .listing_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let exchange_id = event
                    .exchange_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let source_symbol = event
                    .source_symbol
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let previous_status = event
                    .previous_status
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let current_status = event
                    .current_status
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let previous_symbol = event
                    .previous_symbol
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let current_symbol = event
                    .current_symbol
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let record_kind = event
                    .record_kind
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let record_id = event
                    .record_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                FbLifecycleEvent::create(
                    &mut builder,
                    &FbLifecycleEventArgs {
                        event_id: Some(event_id),
                        event_type: Some(event_type),
                        event_time_unix_nanos: event.event_time_unix_nanos,
                        record_kind,
                        record_id,
                        market_id,
                        instrument_id,
                        listing_id,
                        exchange_id,
                        source_symbol,
                        previous_status,
                        current_status,
                        previous_symbol,
                        current_symbol,
                    },
                )
            })
            .collect();
        let event_offsets = builder.create_vector(&event_offsets);
        let market_ids: Vec<_> = events
            .iter()
            .filter_map(|event| event.market_id.as_ref())
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .map(|value| builder.create_string(value))
            .collect();
        let change_kinds: Vec<_> = events
            .iter()
            .map(|event| builder.create_string(&event.event_type))
            .collect();
        let market_ids = builder.create_vector(&market_ids);
        let change_kinds = builder.create_vector(&change_kinds);
        let workspace_id = non_empty_string(&mut builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(&mut builder, &self.identity.launch_id);
        let instance_id = non_empty_string(&mut builder, &self.identity.instance_id);
        let header = MessageHeader::create(
            &mut builder,
            &MessageHeaderArgs {
                message_id: Some(message_id),
                stream_id: Some(stream_id),
                producer_id: Some(producer_id),
                workspace_id,
                launch_id,
                instance_id,
                sequence: catalog.event_sequence,
                event_time_unix_nanos: events
                    .last()
                    .map(|event| event.event_time_unix_nanos)
                    .unwrap_or_else(unix_nanos),
                publish_time_unix_nanos: unix_nanos(),
            },
        );
        let root = FbReferenceChanged::create(
            &mut builder,
            &FbReferenceChangedArgs {
                header: Some(header),
                generation: catalog.generation,
                event_sequence: catalog.event_sequence,
                snapshot_id: Some(snapshot_id),
                events: Some(event_offsets),
                affected_market_ids: Some(market_ids),
                change_kinds: Some(change_kinds),
            },
        );
        finish_reference_changed_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }

    fn market_offsets<'a, 'b, A: flatbuffers::Allocator + 'a>(
        &self,
        builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
        catalog: &ReferenceCatalog,
    ) -> Vec<flatbuffers::WIPOffset<FbMarket<'a>>> {
        catalog
            .markets
            .values()
            .map(|v| {
                let market_id = builder.create_string(&v.market_id);
                let market_key = builder.create_string(&v.market_key);
                let instrument_id = builder.create_string(&v.instrument_id);
                let listing_id = builder.create_string(&v.listing_id);
                let exchange_id = builder.create_string(&v.exchange_id);
                let market_type = builder.create_string(&v.market_type);
                let symbol = builder.create_string(&v.source_symbol);
                let asset_type = v.asset_type.as_ref().map(|x| builder.create_string(x));
                let underlying = v
                    .underlying_instrument_id
                    .as_ref()
                    .map(|x| builder.create_string(x));
                let base = v.base_asset_id.as_ref().map(|x| builder.create_string(x));
                let quote = v.quote_asset_id.as_ref().map(|x| builder.create_string(x));
                let status = builder.create_string(&v.status);
                let price_tick = decimal64(v.price_tick.as_deref());
                let quantity_tick = decimal64(v.quantity_tick.as_deref());
                let minimum_quantity = decimal64(v.minimum_quantity.as_deref());
                let minimum_notional = decimal64(v.minimum_notional.as_deref());
                let contract_size = decimal64(v.contract_size.as_deref());
                FbMarket::create(
                    builder,
                    &FbMarketArgs {
                        market_id: Some(market_id),
                        market_key: Some(market_key),
                        instrument_id: Some(instrument_id),
                        listing_id: Some(listing_id),
                        exchange_id: Some(exchange_id),
                        market_type: Some(market_type),
                        source_symbol: Some(symbol),
                        base_asset_id: base,
                        quote_asset_id: quote,
                        status: Some(status),
                        price_tick: price_tick.as_ref(),
                        quantity_tick: quantity_tick.as_ref(),
                        minimum_quantity: minimum_quantity.as_ref(),
                        minimum_notional: minimum_notional.as_ref(),
                        contract_size: contract_size.as_ref(),
                        price_precision: v.price_precision,
                        quantity_precision: v.quantity_precision,
                        effective_from_unix_nanos: v.effective_from_unix_nanos,
                        effective_to_unix_nanos: v.effective_to_unix_nanos.unwrap_or_default(),
                        asset_type,
                        underlying_instrument_id: underlying,
                    },
                )
            })
            .collect()
    }

    fn header<'a, 'b, A: flatbuffers::Allocator + 'a>(
        &self,
        builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
        view_key: &str,
        catalog: &ReferenceCatalog,
    ) -> flatbuffers::WIPOffset<SnapshotHeader<'a>> {
        let snapshot_id = builder.create_string(&format!("reference:{}", catalog.generation));
        let view = builder.create_string(view_key);
        let actor = builder.create_string(&self.actor_id);
        let stream = builder.create_string(&self.event_stream_id);
        let workspace_id = non_empty_string(builder, &self.identity.workspace_id);
        let launch_id = non_empty_string(builder, &self.identity.launch_id);
        let instance_id = non_empty_string(builder, &self.identity.instance_id);
        SnapshotHeader::create(
            builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view),
                owner_actor_id: Some(actor),
                event_stream_id: Some(stream),
                workspace_id,
                launch_id,
                instance_id,
                event_sequence: catalog.event_sequence,
                version: catalog.generation,
                generation: catalog.generation,
                generated_at_unix_nanos: unix_nanos(),
                as_of_unix_nanos: unix_nanos(),
                complete: true,
            },
        )
    }
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut flatbuffers::FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

fn decimal64(value: Option<&str>) -> Option<Decimal64> {
    let value = value?;
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let digits = format!("{whole}{fraction}");
    let mantissa = digits.parse::<i64>().ok()?;
    Some(Decimal64::new(mantissa, fraction.len() as u8))
}

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::FlatbuffersSnapshotEncoder;
    use crate::model::{FinancialProduct, Instrument, LifecycleEvent, ReferenceCatalog};
    use kairos_protocol::generated::kairos::reference::v_1::{
        root_as_catalog_snapshot, root_as_reference_changed,
    };

    #[test]
    fn catalog_snapshot_round_trips_extended_reference_fields() {
        let catalog = ReferenceCatalog {
            instruments: [(
                "instrument:option:spy:call".into(),
                Instrument {
                    instrument_id: "instrument:option:spy:call".into(),
                    symbol: "O:SPY260821C00600000".into(),
                    instrument_type: "option".into(),
                    underlying_instrument_id: Some("instrument:equity:spy".into()),
                    strike: Some("600.00".into()),
                    option_right: Some("call".into()),
                    status: "active".into(),
                    ..Default::default()
                },
            )]
            .into_iter()
            .collect(),
            financial_products: [(
                "product:binance:earn:btc".into(),
                FinancialProduct {
                    product_id: "product:binance:earn:btc".into(),
                    product_type: "earn".into(),
                    name: "BTC Earn".into(),
                    asset_id: "asset:BTC".into(),
                    provider_product_id: "btc-earn".into(),
                    apr: Some("0.0525".into()),
                    status: "active".into(),
                    effective_from_unix_nanos: 1,
                    ..Default::default()
                },
            )]
            .into_iter()
            .collect(),
            ..Default::default()
        };

        let bytes = FlatbuffersSnapshotEncoder::new("reference-test", "reference.changes")
            .encode_catalog(&catalog)
            .unwrap();
        let snapshot = root_as_catalog_snapshot(&bytes).unwrap();
        let payload = snapshot.payload();
        let product = payload.financial_products().unwrap().get(0);
        assert_eq!(product.product_id(), "product:binance:earn:btc");
        assert_eq!(product.apr().unwrap().mantissa(), 525);
        assert_eq!(product.apr().unwrap().scale(), 4);
        let instrument = payload.instruments().unwrap().get(0);
        assert_eq!(
            instrument.underlying_instrument_id(),
            Some("instrument:equity:spy")
        );
        assert_eq!(instrument.option_right(), Some("call"));
        assert_eq!(instrument.strike().unwrap().mantissa(), 60000);
        assert_eq!(instrument.strike().unwrap().scale(), 2);
    }

    #[test]
    fn change_message_contains_full_lifecycle_events() {
        let event = LifecycleEvent {
            event_id: "event:1".into(),
            event_type: "symbol_changed".into(),
            event_time_unix_nanos: 42,
            record_kind: Some("market".into()),
            record_id: Some("market:1".into()),
            market_id: Some("market:1".into()),
            previous_symbol: Some("OLD".into()),
            current_symbol: Some("NEW".into()),
            ..Default::default()
        };
        let catalog = ReferenceCatalog {
            generation: 3,
            event_sequence: 1,
            ..Default::default()
        };
        let bytes = FlatbuffersSnapshotEncoder::new("reference-test", "reference.events")
            .encode_change(&catalog, &[event])
            .unwrap();
        let message = root_as_reference_changed(&bytes).unwrap();
        let events = message.events().unwrap();
        let value = events.get(0);
        assert_eq!(value.event_id(), "event:1");
        assert_eq!(value.event_type(), "symbol_changed");
        assert_eq!(value.record_kind(), Some("market"));
        assert_eq!(value.record_id(), Some("market:1"));
        assert_eq!(value.current_symbol(), Some("NEW"));
        let decoded = crate::decode_change(&bytes).unwrap();
        assert_eq!(decoded.events[0].record_id.as_deref(), Some("market:1"));
        assert_eq!(decoded.event_sequence, 1);
    }

    #[test]
    fn rust_reads_the_python_generated_reference_golden_fixture() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../../tests/fixtures/reference_catalog_empty.prc1.hex");
        let hex = std::fs::read_to_string(path).unwrap();
        let bytes = hex
            .trim()
            .as_bytes()
            .chunks(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect::<Vec<_>>();
        assert!(kairos_protocol::generated::kairos::reference::v_1::catalog_snapshot_buffer_has_identifier(&bytes));
        let snapshot = root_as_catalog_snapshot(&bytes).unwrap();
        assert_eq!(snapshot.header().snapshot_id(), "reference:0");
        assert_eq!(snapshot.header().view_key(), "reference.catalog");
        assert_eq!(snapshot.payload().entity_count(), 0);
        assert_eq!(snapshot.payload().market_count(), 0);
    }
}
