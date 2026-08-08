//! FlatBuffers encoding for reference read models.

use kairos_protocol::generated::kairos::common::v_1::{
    Decimal64, MessageHeader, MessageHeaderArgs, SnapshotHeader, SnapshotHeaderArgs,
};
use kairos_protocol::generated::kairos::reference::v_1::{
    finish_catalog_snapshot_buffer, finish_lifecycle_snapshot_buffer,
    finish_markets_snapshot_buffer, finish_reference_changed_buffer, Asset as FbAsset,
    AssetArgs as FbAssetArgs, Catalog as FbCatalog, CatalogArgs as FbCatalogArgs,
    CatalogSnapshot as FbCatalogSnapshot, CatalogSnapshotArgs as FbCatalogSnapshotArgs,
    Entity as FbEntity, EntityArgs as FbEntityArgs, FinancialProduct as FbFinancialProduct,
    FinancialProductArgs as FbFinancialProductArgs, Instrument as FbInstrument,
    InstrumentArgs as FbInstrumentArgs, Lifecycle as FbLifecycle, LifecycleArgs as FbLifecycleArgs,
    LifecycleEvent as FbLifecycleEvent, LifecycleEventArgs as FbLifecycleEventArgs,
    LifecycleSnapshot as FbLifecycleSnapshot, LifecycleSnapshotArgs as FbLifecycleSnapshotArgs,
    Listing as FbListing, ListingArgs as FbListingArgs, Market as FbMarket,
    MarketArgs as FbMarketArgs, Markets as FbMarkets, MarketsArgs as FbMarketsArgs,
    MarketsSnapshot as FbMarketsSnapshot, MarketsSnapshotArgs as FbMarketsSnapshotArgs,
    ReferenceChanged as FbReferenceChanged, ReferenceChangedArgs as FbReferenceChangedArgs,
};

use crate::domain::{unix_nanos, LifecycleEvent, ReferenceCatalog, ReferenceResult};

pub(crate) struct FlatbuffersSnapshotEncoder {
    pub actor_id: String,
    pub event_stream_id: String,
}

impl FlatbuffersSnapshotEncoder {
    pub fn new(actor_id: impl Into<String>, event_stream_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            event_stream_id: event_stream_id.into(),
        }
    }

    pub fn encode_catalog(&self, catalog: &ReferenceCatalog) -> ReferenceResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let entity_offsets: Vec<_> = catalog
            .entities
            .values()
            .map(|v| {
                let entity_id = builder.create_string(&v.entity_id);
                let entity_type = builder.create_string(&v.entity_type);
                let name = builder.create_string(&v.name);
                FbEntity::create(
                    &mut builder,
                    &FbEntityArgs {
                        entity_id: Some(entity_id),
                        entity_type: Some(entity_type),
                        name: Some(name),
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
                let venue_id = builder.create_string(&v.venue_id);
                let symbol = builder.create_string(&v.venue_symbol);
                let status = builder.create_string(&v.status);
                FbListing::create(
                    &mut builder,
                    &FbListingArgs {
                        listing_id: Some(listing_id),
                        instrument_id: Some(instrument_id),
                        venue_id: Some(venue_id),
                        venue_symbol: Some(symbol),
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
        let payload = FbCatalog::create(
            &mut builder,
            &FbCatalogArgs {
                entity_count: catalog.entities.len() as u64,
                asset_count: catalog.assets.len() as u64,
                instrument_count: catalog.instruments.len() as u64,
                listing_count: catalog.listings.len() as u64,
                market_count: catalog.markets.len() as u64,
                financial_product_count: catalog.financial_products.len() as u64,
                active_market_count: catalog.active_market_count() as u64,
                lifecycle_event_count: catalog.lifecycle_events.len() as u64,
                entities: Some(entities),
                assets: Some(assets),
                financial_products: Some(financial_products),
                instruments: Some(instruments),
                listings: Some(listings),
                markets: Some(markets),
                ..Default::default()
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

    pub fn encode_markets(&self, catalog: &ReferenceCatalog) -> ReferenceResult<Vec<u8>> {
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

    pub fn encode_lifecycle(&self, catalog: &ReferenceCatalog) -> ReferenceResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let events: Vec<_> = catalog
            .lifecycle_events
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
                let venue_id = event
                    .venue_id
                    .as_ref()
                    .map(|value| builder.create_string(value));
                let symbol = event
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
                FbLifecycleEvent::create(
                    &mut builder,
                    &FbLifecycleEventArgs {
                        event_id: Some(event_id),
                        event_type: Some(event_type),
                        event_time_unix_nanos: event.event_time_unix_nanos,
                        market_id,
                        instrument_id,
                        listing_id,
                        venue_id,
                        source_symbol: symbol,
                        previous_status,
                        current_status,
                        previous_symbol,
                        current_symbol,
                    },
                )
            })
            .collect();
        let events = builder.create_vector(&events);
        let payload = FbLifecycle::create(
            &mut builder,
            &FbLifecycleArgs {
                total_count: catalog.lifecycle_events.len() as u64,
                events: Some(events),
            },
        );
        let header = self.header(&mut builder, "reference.lifecycle", catalog);
        let root = FbLifecycleSnapshot::create(
            &mut builder,
            &FbLifecycleSnapshotArgs {
                header: Some(header),
                payload: Some(payload),
            },
        );
        finish_lifecycle_snapshot_buffer(&mut builder, root);
        Ok(builder.finished_data().to_vec())
    }

    pub fn encode_change(
        &self,
        catalog: &ReferenceCatalog,
        events: &[LifecycleEvent],
    ) -> ReferenceResult<Vec<u8>> {
        let mut builder = flatbuffers::FlatBufferBuilder::new();
        let message_id = builder.create_string(&format!("reference:{}", catalog.event_sequence));
        let stream_id = builder.create_string(&self.event_stream_id);
        let producer_id = builder.create_string(&self.actor_id);
        let snapshot_id = builder.create_string(&format!("reference:{}", catalog.generation));
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
        let header = MessageHeader::create(
            &mut builder,
            &MessageHeaderArgs {
                message_id: Some(message_id),
                stream_id: Some(stream_id),
                producer_id: Some(producer_id),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
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
                let venue_id = builder.create_string(&v.venue_id);
                let market_type = builder.create_string(&v.market_type);
                let symbol = builder.create_string(&v.source_symbol);
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
                        venue_id: Some(venue_id),
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
                        ..Default::default()
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
        SnapshotHeader::create(
            builder,
            &SnapshotHeaderArgs {
                snapshot_id: Some(snapshot_id),
                view_key: Some(view),
                owner_actor_id: Some(actor),
                event_stream_id: Some(stream),
                workspace_id: None,
                launch_id: None,
                instance_id: None,
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

fn decimal64(value: Option<&str>) -> Option<Decimal64> {
    let value = value?;
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let digits = format!("{whole}{fraction}");
    let mantissa = digits.parse::<i64>().ok()?;
    Some(Decimal64::new(mantissa, fraction.len() as u8))
}

#[cfg(test)]
mod tests {
    use super::FlatbuffersSnapshotEncoder;
    use crate::domain::{FinancialProduct, Instrument, ReferenceCatalog};
    use kairos_protocol::generated::kairos::reference::v_1::root_as_catalog_snapshot;

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
                    asset_id: "asset:btc".into(),
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
}
