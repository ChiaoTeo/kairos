//! Binance Reference source and canonical mapping.

use super::*;

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: ConnectionRef,
}

pub struct BinanceOptionsSource {
    connection: ConnectionRef,
}

enum BinanceDerivativesFamily {
    UsdM(ConnectionRef),
    CoinM(ConnectionRef),
}

pub struct BinanceDerivativesSource {
    id: &'static str,
    product: BinanceProduct,
    connection: BinanceDerivativesFamily,
}

pub struct BinanceEquitySource {
    connection: ConnectionRef,
}
impl BinanceSpotSource {
    pub(crate) fn from_key(key: kairos_conflux::ConnectionKey) -> Self {
        Self {
            connection: ConnectionRef::managed(key),
        }
    }
}

impl BinanceOptionsSource {
    pub(crate) fn from_key(key: kairos_conflux::ConnectionKey) -> Self {
        Self {
            connection: ConnectionRef::managed(key),
        }
    }
}

impl BinanceEquitySource {
    pub(crate) fn from_key(key: kairos_conflux::ConnectionKey) -> Self {
        Self {
            connection: ConnectionRef::managed(key),
        }
    }
}

impl BinanceDerivativesSource {
    pub(crate) fn from_usdm_key(key: kairos_conflux::ConnectionKey) -> Self {
        Self {
            id: "binance-usdm-futures",
            product: BinanceProduct::UsdMFutures,
            connection: BinanceDerivativesFamily::UsdM(ConnectionRef::managed(key)),
        }
    }

    pub(crate) fn from_coinm_key(key: kairos_conflux::ConnectionKey) -> Self {
        Self {
            id: "binance-coinm-futures",
            product: BinanceProduct::CoinMFutures,
            connection: BinanceDerivativesFamily::CoinM(ConnectionRef::managed(key)),
        }
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .binance_spot_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceProduct::Spot)
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .binance_options_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceProduct::Option)
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for BinanceDerivativesSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            BinanceDerivativesFamily::UsdM(ConnectionRef(key)) => {
                connections
                    .binance_usdm_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
            BinanceDerivativesFamily::CoinM(ConnectionRef(key)) => {
                connections
                    .binance_coinm_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, self.product)
    }
}

#[async_trait::async_trait(?Send)]
impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    async fn fetch_catalog_with_connections(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ProviderCatalog> {
        let facts = match &mut self.connection {
            ConnectionRef(key) => {
                connections
                    .binance_stocks_rest
                    .get(key)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .fetch_instruments()
                    .await
            },
        }
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_equity_provider_catalog(facts)
    }
}

pub(super) fn binance_equity_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance"
        || facts.participant.kind != ParticipantKind::Broker
    {
        return Err(ReferenceError::Provider(
            "Binance Equity source requires the Binance broker participant".into(),
        ));
    }
    let mut catalog = ProviderCatalog::default();
    for value in facts.instruments {
        if value.kind != ExternalInstrumentKind::Equity {
            return Err(ReferenceError::Provider(format!(
                "Binance Equity catalog contained incompatible instrument kind: {:?}",
                value.kind
            )));
        }
        let symbol = value.source_symbol.as_str().trim().to_ascii_uppercase();
        let status: kairos_primitives::reference::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        let equity_asset =
            kairos_primitives::reference::AssetId::new(format!("asset:equity:{symbol}"))?;
        let instrument_id = kairos_primitives::reference::InstrumentId::new(format!(
            "instrument:equity:US:{symbol}:common"
        ))?;
        catalog.assets.push(Asset {
            asset_id: equity_asset,
            code: kairos_primitives::reference::Symbol::new(symbol.clone())?,
            asset_class: AssetClass::Equity,
            status,
            ..Asset::default()
        });
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_primitives::reference::Symbol::new(symbol.clone())?,
            instrument_type: InstrumentKind::Equity,
            issuer_id: Some(kairos_primitives::reference::IssuerId::new(format!(
                "issuer:US:{symbol}"
            ))?),
            share_class: Some("common".into()),
            status,
            ..Instrument::default()
        });
    }
    catalog.validate()?;
    Ok(catalog)
}

pub(super) fn binance_provider_catalog(
    facts: ExternalInstrumentCatalog,
    instrument_type: BinanceProduct,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance" {
        return Err(ReferenceError::Provider(format!(
            "Binance source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:binance".into(),
            entity_type: "exchange".into(),
            name: "Binance".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        append_binance_instrument(&mut catalog, value, instrument_type)?;
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

fn append_binance_instrument(
    catalog: &mut ProviderCatalog,
    value: ExternalInstrument,
    instrument_type: BinanceProduct,
) -> ReferenceResult<()> {
    let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
    let base = value
        .base_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .ok_or_else(|| ReferenceError::Provider("Binance base currency is missing".into()))?;
    let quote = value
        .quote_currency
        .as_ref()
        .map(|value| value.as_str().to_ascii_uppercase())
        .ok_or_else(|| ReferenceError::Provider("Binance quote currency is missing".into()))?;
    for (code, asset_class) in [
        (
            &base,
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            },
        ),
        (&quote, "crypto"),
    ] {
        catalog.assets.push(Asset {
            asset_id: kairos_primitives::reference::AssetId::new(format!(
                "asset:{asset_class}:{code}"
            ))?,
            code: kairos_primitives::reference::Symbol::new(code.clone())?,
            asset_class: AssetClass::parse_known(asset_class)?,
            status: "active".into(),
            ..Asset::default()
        });
    }
    let (
        _provider_family,
        canonical_family,
        instrument_id,
        canonical_symbol,
        underlying_instrument_id,
    ) = match value.kind {
        ExternalInstrumentKind::Spot if instrument_type == BinanceProduct::Spot => (
            "spot",
            "spot",
            format!("instrument:spot:{base}"),
            base.clone(),
            None,
        ),
        ExternalInstrumentKind::Perpetual
            if matches!(
                instrument_type,
                BinanceProduct::UsdMFutures | BinanceProduct::CoinMFutures
            ) =>
        {
            (
                match instrument_type {
                    BinanceProduct::UsdMFutures => "usd-m-futures",
                    BinanceProduct::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                "perpetual",
                format!("instrument:perpetual:{base}-{quote}"),
                format!("{base}-{quote}"),
                None,
            )
        },
        ExternalInstrumentKind::EquityPerpetual
            if instrument_type == BinanceProduct::UsdMFutures =>
        {
            let underlying = kairos_primitives::reference::InstrumentId::new(format!(
                "instrument:equity:US:{base}:common"
            ))?;
            if !catalog
                .instruments
                .iter()
                .any(|value| value.instrument_id == underlying)
            {
                catalog.instruments.push(Instrument {
                    instrument_id: underlying.clone(),
                    symbol: kairos_primitives::reference::Symbol::new(base.clone())?,
                    instrument_type: InstrumentKind::Equity,
                    issuer_id: Some(kairos_primitives::reference::IssuerId::new(format!(
                        "issuer:US:{base}"
                    ))?),
                    share_class: Some("common".into()),
                    status: "active".into(),
                    ..Instrument::default()
                });
            }
            (
                "usd-m-futures",
                "perpetual",
                format!("instrument:perpetual:equity:US:{base}:{quote}"),
                format!("{base}-{quote}"),
                Some(underlying),
            )
        },
        ExternalInstrumentKind::Future
            if matches!(
                instrument_type,
                BinanceProduct::UsdMFutures | BinanceProduct::CoinMFutures
            ) =>
        {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            (
                match instrument_type {
                    BinanceProduct::UsdMFutures => "usd-m-futures",
                    BinanceProduct::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                "future",
                format!("instrument:future:{base}-{quote}:{expiry}"),
                format!("{base}-{quote}-{expiry}"),
                None,
            )
        },
        ExternalInstrumentKind::Option if instrument_type == BinanceProduct::Option => {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            let strike = value.strike.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option strike is missing".into())
            })?;
            let right = value.option_right.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option right is missing".into())
            })?;
            let underlying =
                kairos_primitives::reference::InstrumentId::new(format!("instrument:spot:{base}"))?;
            if !catalog
                .instruments
                .iter()
                .any(|value| value.instrument_id == underlying)
            {
                catalog.instruments.push(Instrument {
                    instrument_id: underlying.clone(),
                    symbol: kairos_primitives::reference::Symbol::new(base.clone())?,
                    instrument_type: InstrumentKind::Spot,
                    primary_currency_asset_id: Some(kairos_primitives::reference::AssetId::new(
                        format!("asset:crypto:{base}"),
                    )?),
                    status: "active".into(),
                    ..Instrument::default()
                });
            }
            (
                "options",
                "option",
                format!(
                    "instrument:option:{base}-{quote}:{expiry}:{strike}:{}",
                    match right.to_ascii_lowercase().as_str() {
                        "call" | "c" => "C",
                        "put" | "p" => "P",
                        _ => {
                            return Err(ReferenceError::Provider(format!(
                                "unsupported Binance option right: {right}"
                            )));
                        },
                    }
                ),
                format!(
                    "{base}-{quote}-{expiry}-{strike}-{}",
                    match right.to_ascii_lowercase().as_str() {
                        "call" | "c" => "C",
                        "put" | "p" => "P",
                        _ => unreachable!("option right validated above"),
                    }
                ),
                Some(underlying),
            )
        },
        other => {
            return Err(ReferenceError::Provider(format!(
                "Binance {instrument_type:?} catalog contained incompatible instrument kind: {other:?}"
            )));
        },
    };
    let instrument_id = kairos_primitives::reference::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_primitives::reference::ListingId::new(if canonical_family == "spot" {
        format!("listing:binance:spot:{base}:{quote}")
    } else {
        format!("listing:binance:{canonical_family}:{source_symbol}")
    })?;
    let exchange_id = kairos_primitives::reference::Exchange::new("exchange:binance")?;
    let market_id = kairos_primitives::reference::MarketId::new(format!(
        "market:binance:{canonical_family}:{source_symbol}"
    ))?;
    let instrument_kind = canonical_instrument_kind(value.kind)?;
    let status: kairos_primitives::reference::ReferenceStatus =
        if value.active { "active" } else { "inactive" }.into();
    catalog.instruments.push(Instrument {
        instrument_id: instrument_id.clone(),
        symbol: kairos_primitives::reference::Symbol::new(canonical_symbol)?,
        instrument_type: instrument_kind,
        primary_currency_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
            "asset:crypto:{}",
            if canonical_family == "spot" {
                &base
            } else {
                &quote
            }
        ))?),
        underlying_instrument_id: underlying_instrument_id.clone(),
        expiry_unix_nanos: matches!(
            value.kind,
            ExternalInstrumentKind::Future | ExternalInstrumentKind::Option
        )
        .then_some(value.expiry_unix_nanos)
        .flatten(),
        strike: super::optional_decimal(value.strike.clone(), "Binance option strike")?,
        option_right: value.option_right.clone(),
        status,
        ..Instrument::default()
    });
    catalog.listings.push(Listing {
        source_id: Some(instrument_type.source_id().into()),
        listing_id: listing_id.clone(),
        instrument_id: instrument_id.clone(),
        exchange_id: exchange_id.clone(),
        exchange_symbol: kairos_primitives::reference::Symbol::new(source_symbol.clone())?,
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
        asset_type: Some(if value.kind == ExternalInstrumentKind::EquityPerpetual {
            AssetClass::Equity
        } else {
            AssetClass::Crypto
        }),
        venue_symbol: Some(kairos_primitives::reference::Symbol::new(source_symbol)?),
        base_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
            "asset:{}:{base}",
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            }
        ))?),
        quote_asset_id: Some(kairos_primitives::reference::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        status,
        price_tick: super::optional_decimal(value.price_tick, "Binance price tick")?,
        quantity_tick: super::optional_decimal(value.quantity_tick, "Binance quantity tick")?,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        minimum_quantity: super::optional_decimal(
            value.minimum_quantity,
            "Binance minimum quantity",
        )?,
        minimum_notional: super::optional_decimal(
            value.minimum_notional,
            "Binance minimum notional",
        )?,
        contract_size: super::optional_decimal(value.contract_value, "Binance contract size")?,
        underlying_instrument_id,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Market::default()
    });
    Ok(())
}
