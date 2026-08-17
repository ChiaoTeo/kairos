//! Binance Reference source and canonical mapping.

use super::*;

/// Binance Spot public reference source.
///
/// The provider connection and vendor normalization live in integration. This
/// source only maps the neutral integration payload into Reference-owned
/// domain records.
pub struct BinanceSpotSource {
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceOptionsSource {
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceDerivativesSource {
    id: &'static str,
    instrument_type: BinanceInstrumentType,
    connection: kairos_integration::participants::binance::BinanceInstrumentCatalog,
}

pub struct BinanceEquitySource {
    connection: BinanceEquityInstrumentCatalog,
}
impl BinanceSpotSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let config = binance_spot_public_config(endpoint)?;
        Ok(Self {
            connection: BinanceSpotConnection::connect(config)
                .map_err(|error| ReferenceError::Provider(error.to_string()))?
                .instrument_catalog(),
        })
    }
}

impl BinanceOptionsSource {
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        let config = binance_options_public_config(endpoint)?;
        Ok(Self {
            connection: BinanceOptionsConnection::connect(config)
                .map_err(|error| ReferenceError::Provider(error.to_string()))?
                .instrument_catalog(),
        })
    }
}

impl BinanceEquitySource {
    pub fn new(
        endpoint: impl Into<String>,
        api_key: secrecy::SecretString,
    ) -> ReferenceResult<Self> {
        let provider = BinanceSpotConnection::connect(binance_spot_public_config(endpoint)?)
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        Ok(Self {
            connection: provider.equity_instrument_catalog(api_key),
        })
    }
}

impl BinanceDerivativesSource {
    pub fn new(
        instrument_type: BinanceInstrumentType,
        endpoint: impl Into<String>,
    ) -> ReferenceResult<Self> {
        let id = match instrument_type {
            BinanceInstrumentType::UsdMFutures => "binance-usdm-futures",
            BinanceInstrumentType::CoinMFutures => "binance-coinm-futures",
            _ => {
                return Err(ReferenceError::Provider(
                    "Binance derivatives source requires a futures instrument type".into(),
                ))
            }
        };
        let config = binance_futures_public_config(endpoint)?;
        Ok(Self {
            id,
            instrument_type,
            connection: match instrument_type {
                BinanceInstrumentType::UsdMFutures => {
                    BinanceUsdMConnection::connect(config.clone())
                        .map_err(|error| ReferenceError::Provider(error.to_string()))?
                        .instrument_catalog()
                }
                BinanceInstrumentType::CoinMFutures => BinanceCoinMConnection::connect(config)
                    .map_err(|error| ReferenceError::Provider(error.to_string()))?
                    .instrument_catalog(),
                _ => unreachable!("validated Binance derivatives instrument type"),
            },
        })
    }
}

fn binance_public_base_url(endpoint: impl Into<String>) -> String {
    let endpoint = endpoint.into();
    let trimmed = endpoint.trim_end_matches('/');
    let base_url = trimmed
        .strip_suffix("/api/v3/exchangeInfo")
        .or_else(|| trimmed.strip_suffix("/fapi/v1/exchangeInfo"))
        .or_else(|| trimmed.strip_suffix("/dapi/v1/exchangeInfo"))
        .or_else(|| trimmed.strip_suffix("/eapi/v1/exchangeInfo"))
        .unwrap_or(trimmed)
        .to_owned();
    base_url
}

fn binance_spot_public_config(
    endpoint: impl Into<String>,
) -> ReferenceResult<BinanceSpotConnectionConfig> {
    Ok(BinanceSpotConnectionConfig {
        environment: "public".into(),
        rest_base_url: binance_public_base_url(endpoint),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_200,
            cancel_reserve_weight: 0,
        },
        shared_quota: None,
    })
}

fn binance_futures_public_config(
    endpoint: impl Into<String>,
) -> ReferenceResult<BinanceFuturesConnectionConfig> {
    Ok(BinanceFuturesConnectionConfig {
        environment: "public".into(),
        rest_base_url: binance_public_base_url(endpoint),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_200,
            cancel_reserve_weight: 0,
        },
        shared_quota: None,
    })
}

fn binance_options_public_config(
    endpoint: impl Into<String>,
) -> ReferenceResult<BinanceOptionsConnectionConfig> {
    Ok(BinanceOptionsConnectionConfig {
        environment: "public".into(),
        rest_base_url: binance_public_base_url(endpoint),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_200,
            cancel_reserve_weight: 0,
        },
        shared_quota: None,
    })
}

#[async_trait::async_trait]
impl ReferenceSource for BinanceSpotSource {
    fn source_id(&self) -> &str {
        "binance-spot"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Spot)
    }
}

#[async_trait::async_trait]
impl ReferenceSource for BinanceOptionsSource {
    fn source_id(&self) -> &str {
        "binance-options"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, BinanceInstrumentType::Option)
    }
}

#[async_trait::async_trait]
impl ReferenceSource for BinanceDerivativesSource {
    fn source_id(&self) -> &str {
        self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_provider_catalog(facts, self.instrument_type)
    }
}

#[async_trait::async_trait]
impl ReferenceSource for BinanceEquitySource {
    fn source_id(&self) -> &str {
        "binance-equity"
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = self
            .connection
            .fetch_instruments()
            .await
            .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        binance_equity_provider_catalog(facts)
    }
}

pub(super) fn binance_equity_provider_catalog(
    facts: ExternalInstrumentCatalog,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "binance"
        || facts.participant.kind
            != kairos_integration::application::capabilities::ParticipantKind::Broker
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
        let status: kairos_primitives::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        let equity_asset = kairos_primitives::AssetId::new(format!("asset:equity:{symbol}"))?;
        let instrument_id =
            kairos_primitives::InstrumentId::new(format!("instrument:equity:US:{symbol}:common"))?;
        catalog.assets.push(Asset {
            asset_id: equity_asset,
            code: symbol.clone(),
            asset_class: AssetClass::Equity,
            status,
            ..Asset::default()
        });
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_primitives::Symbol::new(symbol.clone())?,
            instrument_type: InstrumentKind::Equity,
            issuer_id: Some(kairos_primitives::IssuerId::new(format!(
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
    instrument_type: BinanceInstrumentType,
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
    instrument_type: BinanceInstrumentType,
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
            asset_id: kairos_primitives::AssetId::new(format!("asset:{asset_class}:{code}"))?,
            code: code.clone(),
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
        ExternalInstrumentKind::Spot if instrument_type == BinanceInstrumentType::Spot => (
            "spot",
            "spot",
            format!("instrument:spot:{base}"),
            base.clone(),
            None,
        ),
        ExternalInstrumentKind::Perpetual
            if matches!(
                instrument_type,
                BinanceInstrumentType::UsdMFutures | BinanceInstrumentType::CoinMFutures
            ) =>
        {
            (
                match instrument_type {
                    BinanceInstrumentType::UsdMFutures => "usd-m-futures",
                    BinanceInstrumentType::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                "perpetual",
                format!("instrument:perpetual:{base}-{quote}"),
                format!("{base}-{quote}"),
                None,
            )
        }
        ExternalInstrumentKind::EquityPerpetual
            if instrument_type == BinanceInstrumentType::UsdMFutures =>
        {
            let underlying = kairos_primitives::InstrumentId::new(format!(
                "instrument:equity:US:{base}:common"
            ))?;
            if !catalog
                .instruments
                .iter()
                .any(|value| value.instrument_id == underlying)
            {
                catalog.instruments.push(Instrument {
                    instrument_id: underlying.clone(),
                    symbol: kairos_primitives::Symbol::new(base.clone())?,
                    instrument_type: InstrumentKind::Equity,
                    issuer_id: Some(kairos_primitives::IssuerId::new(format!(
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
        }
        ExternalInstrumentKind::Future
            if matches!(
                instrument_type,
                BinanceInstrumentType::UsdMFutures | BinanceInstrumentType::CoinMFutures
            ) =>
        {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            (
                match instrument_type {
                    BinanceInstrumentType::UsdMFutures => "usd-m-futures",
                    BinanceInstrumentType::CoinMFutures => "coin-m-futures",
                    _ => unreachable!("guarded futures type"),
                },
                "future",
                format!("instrument:future:{base}-{quote}:{expiry}"),
                format!("{base}-{quote}-{expiry}"),
                None,
            )
        }
        ExternalInstrumentKind::Option if instrument_type == BinanceInstrumentType::Option => {
            let expiry = canonical_expiry(value.expiry_unix_nanos)?;
            let strike = value.strike.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option strike is missing".into())
            })?;
            let right = value.option_right.as_deref().ok_or_else(|| {
                ReferenceError::Provider("Binance option right is missing".into())
            })?;
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
                    match right.to_ascii_lowercase().as_str() {
                        "call" | "c" => "C",
                        "put" | "p" => "P",
                        _ => {
                            return Err(ReferenceError::Provider(format!(
                                "unsupported Binance option right: {right}"
                            )));
                        }
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
        }
        other => {
            return Err(ReferenceError::Provider(format!(
            "Binance {instrument_type:?} catalog contained incompatible instrument kind: {other:?}"
        )))
        }
    };
    let instrument_id = kairos_primitives::InstrumentId::new(instrument_id)?;
    let listing_id = kairos_primitives::ListingId::new(if canonical_family == "spot" {
        format!("listing:binance:spot:{base}:{quote}")
    } else {
        format!("listing:binance:{canonical_family}:{source_symbol}")
    })?;
    let exchange_id = kairos_primitives::Exchange::new("exchange:binance")?;
    let market_id = kairos_primitives::MarketId::new(format!(
        "market:binance:{canonical_family}:{source_symbol}"
    ))?;
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
        underlying_instrument_id: underlying_instrument_id.clone(),
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
        asset_type: Some(if value.kind == ExternalInstrumentKind::EquityPerpetual {
            AssetClass::Equity
        } else {
            AssetClass::Crypto
        }),
        venue_symbol: Some(kairos_primitives::Symbol::new(source_symbol)?),
        base_asset_id: Some(kairos_primitives::AssetId::new(format!(
            "asset:{}:{base}",
            if value.kind == ExternalInstrumentKind::EquityPerpetual {
                "equity"
            } else {
                "crypto"
            }
        ))?),
        quote_asset_id: Some(kairos_primitives::AssetId::new(format!(
            "asset:crypto:{quote}"
        ))?),
        status,
        price_tick: value.price_tick,
        quantity_tick: value.quantity_tick,
        price_precision: value.price_precision.unwrap_or_default() as i32,
        quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
        minimum_quantity: value.minimum_quantity,
        minimum_notional: value.minimum_notional,
        contract_size: value.contract_value,
        underlying_instrument_id,
        effective_from_unix_nanos: 0.into(),
        effective_to_unix_nanos: value.expiry_unix_nanos,
        ..Market::default()
    });
    Ok(())
}
