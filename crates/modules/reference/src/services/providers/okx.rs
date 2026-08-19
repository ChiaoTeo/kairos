//! OKX Reference source and canonical mapping.

use super::*;

pub struct OkxSource {
    id: String,
    product: OkxProduct,
    connection: ConnectionRef,
}
impl OkxSource {
    pub(crate) fn from_key(
        id: impl Into<String>,
        product: OkxProduct,
        key: kairos_conflux::ConnectionKey,
    ) -> Self {
        Self {
            id: id.into(),
            product,
            connection: ConnectionRef::managed(key),
        }
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for OkxSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let instrument_type = match self.product {
            OkxProduct::Spot => "SPOT",
            OkxProduct::Margin => "MARGIN",
            OkxProduct::Swap => "SWAP",
            OkxProduct::Futures => "FUTURES",
            OkxProduct::Option => "OPTION",
        };
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .okx_public_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments_by_type(instrument_type)
                    .await
            }
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        okx_provider_catalog(facts)
    }
}

pub(super) fn okx_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "okx" {
        return Err(ReferenceError::Provider(format!(
            "OKX source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:okx".into(),
            entity_type: "exchange".into(),
            name: "OKX".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        append_okx_instrument(&mut catalog, value)?;
    }
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    crate::domain::reconcile_instruments(&mut catalog.instruments)?;
    catalog.validate()?;
    Ok(catalog)
}

fn append_okx_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let (base, quote) = okx_base_quote(&value)?;
    let (
        _provider_family,
        canonical_family,
        instrument_id,
        canonical_symbol,
        underlying_instrument_id,
    ) =
        match value.kind {
            ExternalInstrumentKind::Equity | ExternalInstrumentKind::EquityPerpetual => {
                return Err(ReferenceError::Provider(
                    "OKX catalog cannot contain Binance equity instrument kinds".into(),
                ))
            }
            ExternalInstrumentKind::Spot => (
                "spot",
                "spot",
                format!("instrument:spot:{base}"),
                base.clone(),
                None,
            ),
            ExternalInstrumentKind::Margin => (
                "margin",
                "spot",
                format!("instrument:spot:{base}"),
                base.clone(),
                None,
            ),
            ExternalInstrumentKind::Perpetual => (
                "swap",
                "perpetual",
                format!("instrument:perpetual:{base}-{quote}"),
                format!("{base}-{quote}"),
                None,
            ),
            ExternalInstrumentKind::Future => {
                let expiry = canonical_expiry(value.expiry_unix_nanos)?;
                (
                    "futures",
                    "future",
                    format!("instrument:future:{base}-{quote}:{expiry}"),
                    format!("{base}-{quote}-{expiry}"),
                    None,
                )
            }
            ExternalInstrumentKind::Option => {
                let expiry = canonical_expiry(value.expiry_unix_nanos)?;
                let strike = value.strike.as_deref().ok_or_else(|| {
                    ReferenceError::Provider("OKX option strike is missing".into())
                })?;
                let right = value.option_right.as_deref().ok_or_else(|| {
                    ReferenceError::Provider("OKX option right is missing".into())
                })?;
                let canonical_right = right.to_ascii_uppercase();
                let underlying =
                    kairos_primitives::InstrumentId::new(format!("instrument:spot:{base}"))?;
                if !catalog
                    .instruments
                    .iter()
                    .any(|value| value.instrument_id == underlying)
                {
                    catalog.instruments.push(Instrument {
                        instrument_id: underlying.clone(),
                        symbol: kairos_primitives::Symbol::new(base.clone())?,
                        instrument_type: InstrumentKind::Spot,
                        primary_currency_asset_id: Some(kairos_primitives::AssetId::new(format!(
                            "asset:crypto:{base}"
                        ))?),
                        status: "active".into(),
                        ..Instrument::default()
                    });
                }
                (
                    "options",
                    "option",
                    format!(
                        "instrument:option:{base}-{quote}:{expiry}:{strike}:{}",
                        canonical_right
                    ),
                    format!("{base}-{quote}-{expiry}-{strike}-{canonical_right}"),
                    Some(underlying),
                )
            }
        };
    for code in [&base, &quote] {
        catalog.assets.push(Asset {
            asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))?,
            code: code.clone(),
            asset_class: AssetClass::Crypto,
            status: "active".into(),
            ..Asset::default()
        });
    }
    let instrument_id = kairos_primitives::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_primitives::ListingId::new(if canonical_family == "spot" {
        format!("listing:okx:spot:{base}:{quote}")
    } else {
        format!("listing:okx:{canonical_family}:{source_symbol}")
    })?;
    let exchange_id = kairos_primitives::Exchange::new("exchange:okx")?;
    let market_id =
        kairos_primitives::MarketId::new(format!("market:okx:{canonical_family}:{source_symbol}"))?;
    let instrument_kind = canonical_instrument_kind(value.kind)?;
    let status: kairos_primitives::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::Symbol::new(canonical_symbol)?,
        instrument_type: instrument_kind,
        primary_currency_asset_id: Some(kairos_primitives::AssetId::new(format!(
            "asset:crypto:{}",
            if canonical_family == "spot" {
                &base
            } else {
                &quote
            }
        ))?),
        underlying_instrument_id,
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
        strike: value.strike.clone(),
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange_id.clone(),
        exchange_symbol: kairos_primitives::Symbol::new(source_symbol.clone())?,
        status,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Listing::default()
    });
    catalog.markets.push(Market {
        market_id,
        instrument_id,
        listing_id: Some(listing_id),
        exchange_id,
        instrument_kind,
        asset_type: Some(AssetClass::Crypto),
        venue_symbol: Some(kairos_primitives::Symbol::new(source_symbol)?),
        base_asset_id: Some(kairos_primitives::AssetId::new(format!(
            "asset:crypto:{base}"
        ))?),
        quote_asset_id: Some(kairos_primitives::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        contract_size: value.contract_value,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Market::default()
    });
    Ok(())
}

fn okx_base_quote(value: &ExternalInstrument) -> ReferenceResult<(String, String)> {
    let from_pair = |pair: &str| {
        let mut values = pair.split('-').filter(|value| !value.is_empty());
        values
            .next()
            .zip(values.next())
            .map(|(base, quote)| (base.to_ascii_uppercase(), quote.to_ascii_uppercase()))
    };
    let fallback = value
        .underlying
        .as_ref()
        .and_then(|value| from_pair(value.as_str()))
        .or_else(|| from_pair(value.source_symbol.as_str()));
    let base = value
        .base_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .or_else(|| fallback.as_ref().map(|value| value.0.clone()))
        .ok_or_else(|| {
            ReferenceError::Provider("OKX instrument base currency is missing".into())
        })?;
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .or_else(|| fallback.as_ref().map(|value| value.1.clone()))
        .or_else(|| {
            value
                .settlement_currency
                .as_ref()
                .map(|value| value.as_str().to_ascii_uppercase())
        })
        .ok_or_else(|| {
            ReferenceError::Provider("OKX instrument quote currency is missing".into())
        })?;
    Ok((base, quote))
}
