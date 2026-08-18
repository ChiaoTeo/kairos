//! Hyperliquid Reference source and canonical mapping.

use super::*;

pub struct HyperliquidSource {
    id: String,
    product: HyperliquidProduct,
    connection: HyperliquidInfoRestConnection,
}
impl HyperliquidSource {
    pub(crate) fn from_connection(
        product: HyperliquidProduct,
        connection: HyperliquidInfoRestConnection,
    ) -> Self {
        let id = match product {
            HyperliquidProduct::Perpetual => "hyperliquid-perpetual",
            HyperliquidProduct::Spot => "hyperliquid-spot",
        };
        Self {
            id: id.into(),
            product,
            connection,
        }
    }

    #[cfg(test)]
    pub fn new(endpoint: impl Into<String>) -> ReferenceResult<Self> {
        Self::for_product(endpoint, HyperliquidProduct::Perpetual)
    }

    #[cfg(test)]
    fn for_product(
        endpoint: impl Into<String>,
        product: HyperliquidProduct,
    ) -> ReferenceResult<Self> {
        let connection = HyperliquidInfoRestConnection::new(HyperliquidRestConfig {
            binding_id: match product {
                HyperliquidProduct::Perpetual => "reference-hyperliquid-perpetual",
                HyperliquidProduct::Spot => "reference-hyperliquid-spot",
            }
            .into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
        })
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
        let id = match product {
            HyperliquidProduct::Perpetual => "hyperliquid-perpetual",
            HyperliquidProduct::Spot => "hyperliquid-spot",
        };
        Ok(Self {
            id: id.into(),
            product,
            connection,
        })
    }
}

#[async_trait::async_trait]
impl ReferenceSource for HyperliquidSource {
    fn source_id(&self) -> &str {
        &self.id
    }

    async fn fetch_catalog(&mut self) -> ReferenceResult<ProviderCatalog> {
        let facts = match self.product {
            HyperliquidProduct::Perpetual => self.connection.fetch_perpetual_instruments().await,
            HyperliquidProduct::Spot => self.connection.fetch_spot_instruments().await,
        };
        let facts = facts.map_err(|error| ReferenceError::Provider(error.to_string()))?;
        hyperliquid_provider_catalog(facts, self.product)
    }
}

pub(super) fn hyperliquid_provider_catalog(
    facts: ExternalInstrumentCatalog,
    product: HyperliquidProduct,
) -> ReferenceResult<ProviderCatalog> {
    if facts.participant.id.as_str() != "hyperliquid" {
        return Err(ReferenceError::Provider(format!(
            "Hyperliquid source received catalog for {}",
            facts.participant.id
        )));
    }
    let mut catalog = ProviderCatalog {
        entities: vec![Entity {
            entity_id: "exchange:hyperliquid".into(),
            entity_type: "exchange".into(),
            name: "Hyperliquid".into(),
            status: "active".into(),
            source_id: None,
        }],
        ..Default::default()
    };
    for value in facts.instruments {
        let (expected_kind, instrument_kind, family) = match product {
            HyperliquidProduct::Perpetual => (
                ExternalInstrumentKind::Perpetual,
                InstrumentKind::Perpetual,
                "perpetual",
            ),
            HyperliquidProduct::Spot => {
                (ExternalInstrumentKind::Spot, InstrumentKind::Spot, "spot")
            }
        };
        if value.kind != expected_kind {
            return Err(ReferenceError::Provider(format!(
                "unsupported Hyperliquid instrument kind: {:?}",
                value.kind
            )));
        }
        let source_symbol = value.source_symbol.as_str().to_ascii_uppercase();
        let base = value
            .base_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .ok_or_else(|| {
                ReferenceError::Provider("Hyperliquid base currency is missing".into())
            })?;
        let quote = value
            .quote_currency
            .as_ref()
            .map(|value| value.as_str().to_ascii_uppercase())
            .unwrap_or_else(|| "USDC".into());
        for code in [&base, &quote] {
            catalog.assets.push(Asset {
                asset_id: kairos_primitives::AssetId::new(format!("asset:crypto:{code}"))?,
                code: code.clone(),
                asset_class: AssetClass::Crypto,
                status: "active".into(),
                ..Asset::default()
            });
        }
        let instrument_id = match product {
            HyperliquidProduct::Perpetual => kairos_primitives::InstrumentId::new(format!(
                "instrument:perpetual:{base}-{quote}"
            ))?,
            HyperliquidProduct::Spot => {
                kairos_primitives::InstrumentId::new(format!("instrument:spot:{base}"))?
            }
        };
        let listing_id = kairos_primitives::ListingId::new(format!(
            "listing:hyperliquid:{family}:{base}:{quote}"
        ))?;
        let exchange_id = kairos_primitives::Exchange::new("exchange:hyperliquid")?;
        let status: kairos_primitives::ReferenceStatus =
            if value.active { "active" } else { "inactive" }.into();
        catalog.instruments.push(Instrument {
            instrument_id: instrument_id.clone(),
            symbol: kairos_primitives::Symbol::new(format!("{base}-{quote}"))?,
            instrument_type: instrument_kind,
            primary_currency_asset_id: Some(kairos_primitives::AssetId::new(format!(
                "asset:crypto:{base}"
            ))?),
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
            ..Listing::default()
        });
        catalog.markets.push(Market {
            market_id: kairos_primitives::MarketId::new(format!(
                "market:hyperliquid:{family}:{source_symbol}"
            ))?,
            instrument_id: instrument_id.clone(),
            listing_id: Some(listing_id.clone()),
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
            price_precision: value.price_precision.unwrap_or_default() as i32,
            quantity_precision: value.quantity_precision.unwrap_or_default() as i32,
            minimum_quantity: value.minimum_quantity,
            minimum_notional: value.minimum_notional,
            contract_size: value.contract_value,
            effective_from_unix_nanos: 0.into(),
            ..Market::default()
        });
    }
    catalog
        .assets
        .sort_by(|left, right| left.asset_id.cmp(&right.asset_id));
    catalog
        .assets
        .dedup_by(|left, right| left.asset_id == right.asset_id);
    catalog.validate()?;
    Ok(catalog)
}
