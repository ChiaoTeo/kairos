//! The single public composition facade for integrations.
//!
//! Callers configure one `Integration` instance and receive only stable
//! protocol traits.  Provider factories, registry entries and concrete
//! connection types never cross this boundary.

use crate::application::reference::ReferenceDataConnection;
use crate::application::{
    AccountCredentialInspectionConnection, AccountEventStreamConnection,
    AccountMarketProfileConnection, AccountReadConnection, ConnectionSpec, EarnConnection,
    ExecutionStreamConnection, IntegrationError, MarketStreamConnection, OrderEntryConnection,
    OrderQueryConnection, TransferConnection,
};
use crate::domain::{IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind};
use crate::services::factories::GatewayRegistry;
use crate::services::gateways::binance::{self, equity};
use crate::services::gateways::hyperliquid::HyperliquidReferenceConnection;
use crate::services::gateways::massive::{MassiveReferenceConnection, MassiveStocksRestClient};
use crate::services::gateways::okx::{
    OkxAccountConnection, OkxAccountMarketProfileConnection, OkxOrderConnection,
};
use crate::services::gateways::okx_stream::OkxAccountStreamConnection;
use crate::services::gateways::public_reference::{
    binance_derivatives_catalog, okx_catalog, PublicReferenceConnection,
};

#[path = "integration_connections.rs"]
mod integration_connections;

pub(super) struct MarketStreamEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn MarketStreamConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct ReferenceEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) asset_type: Option<crate::domain::AssetType>,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn ReferenceDataConnection>, IntegrationError> + Send + Sync>,
}

/// Configured integration composition root.
pub struct Integration {
    pub(super) gateways: GatewayRegistry,
    pub(super) references: Vec<ReferenceEntry>,
    pub(super) market_streams: Vec<MarketStreamEntry>,
    pub(super) accounts: Vec<AccountEntry>,
    pub(super) account_market_profiles: Vec<AccountMarketProfileEntry>,
    pub(super) order_entries: Vec<OrderEntry>,
    pub(super) order_queries: Vec<OrderQueryEntry>,
    pub(super) account_streams: Vec<AccountStreamEntry>,
    pub(super) execution_streams: Vec<ExecutionStreamEntry>,
    pub(super) credential_inspections: Vec<CredentialInspectionEntry>,
    pub(super) earns: Vec<EarnEntry>,
    pub(super) transfers: Vec<TransferEntry>,
}

pub(super) struct AccountEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn AccountReadConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct AccountMarketProfileEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open: Box<
        dyn Fn() -> Result<Box<dyn AccountMarketProfileConnection>, IntegrationError> + Send + Sync,
    >,
}

pub(super) struct OrderEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn OrderEntryConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct OrderQueryEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn OrderQueryConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct AccountStreamEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open: Box<
        dyn Fn() -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> + Send + Sync,
    >,
}

pub(super) struct ExecutionStreamEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn ExecutionStreamConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct CredentialInspectionEntry {
    pub(super) route: IntegrationRoute,
    pub(super) product: Option<ProductFamily>,
    pub(super) transport: TransportKind,
    pub(super) open: Box<
        dyn Fn() -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError>
            + Send
            + Sync,
    >,
}

pub(super) struct EarnEntry {
    pub(super) route: IntegrationRoute,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn EarnConnection>, IntegrationError> + Send + Sync>,
}

pub(super) struct TransferEntry {
    pub(super) route: IntegrationRoute,
    pub(super) transport: TransportKind,
    pub(super) open:
        Box<dyn Fn() -> Result<Box<dyn TransferConnection>, IntegrationError> + Send + Sync>,
}

impl Integration {
    pub fn new() -> Self {
        Self {
            gateways: GatewayRegistry::new(),
            references: Vec::new(),
            market_streams: Vec::new(),
            accounts: Vec::new(),
            account_market_profiles: Vec::new(),
            order_entries: Vec::new(),
            order_queries: Vec::new(),
            account_streams: Vec::new(),
            execution_streams: Vec::new(),
            credential_inspections: Vec::new(),
            earns: Vec::new(),
            transfers: Vec::new(),
        }
    }

    /// Adds Binance reference connections without exposing the Binance
    /// factory or connection types to the caller.
    pub fn with_binance_reference(
        mut self,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let factory = binance::BinanceReferenceFactory::new(endpoint)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let reference_factory = factory.clone();
        self.gateways.register(factory);
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            asset_type: None,
            open: Box::new(move || {
                reference_factory
                    .open()
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_options_reference(
        mut self,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let factory = binance::BinanceReferenceFactory::options(endpoint)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
        let reference_factory = factory.clone();
        self.gateways.register(factory);
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Options),
            transport: TransportKind::Rest,
            asset_type: None,
            open: Box::new(move || {
                reference_factory
                    .open()
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_derivatives_reference(
        mut self,
        product: ProductFamily,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance derivatives reference requires futures product".into(),
            ));
        }
        let endpoint = endpoint.into();
        let connection = move || {
            PublicReferenceConnection::new(
                crate::domain::IntegrationRoute::exchange("binance"),
                product,
                Some(crate::domain::AssetType::Crypto),
                endpoint.clone(),
                binance_derivatives_catalog,
            )
            .map(|value| Box::new(value) as Box<dyn ReferenceDataConnection>)
            .map_err(IntegrationError::InvalidRequest)
        };
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            asset_type: Some(crate::domain::AssetType::Crypto),
            open: Box::new(connection),
        });
        Ok(self)
    }

    pub fn with_okx_reference(
        mut self,
        product: ProductFamily,
        asset_type: Option<crate::domain::AssetType>,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "unsupported OKX reference product".into(),
            ));
        }
        let endpoint = endpoint.into().trim_end_matches('/').to_string();
        let inst_type = match product {
            ProductFamily::Spot => "SPOT",
            ProductFamily::UsdMFutures => "SWAP",
            ProductFamily::CoinMFutures => "FUTURES",
            ProductFamily::Options => "OPTION",
            _ => unreachable!(),
        };
        let endpoint = format!("{endpoint}/api/v5/public/instruments?instType={inst_type}");
        let connection = move || {
            PublicReferenceConnection::new(
                crate::domain::IntegrationRoute::exchange("okx"),
                product,
                asset_type,
                endpoint.clone(),
                okx_catalog,
            )
            .map(|value| Box::new(value) as Box<dyn ReferenceDataConnection>)
            .map_err(IntegrationError::InvalidRequest)
        };
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            asset_type,
            open: Box::new(connection),
        });
        Ok(self)
    }

    /// Adds Binance Equity reference connections without exposing provider
    /// implementation types.
    pub fn with_binance_equity(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
    ) -> Self {
        let factory = equity::BinanceEquityReferenceFactory::new(api_key, secret);
        let reference_factory = factory.clone();
        self.gateways.register(factory);
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            transport: TransportKind::Rest,
            asset_type: Some(crate::domain::AssetType::Equity),
            open: Box::new(move || {
                reference_factory
                    .open_connection()
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_massive_reference(
        mut self,
        api_key: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let client = MassiveStocksRestClient::with_base_url(api_key, base_url)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?
            .for_options();
        let reference_client = client.clone();
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::data_provider("massive"),
            product: None,
            transport: TransportKind::Rest,
            asset_type: Some(crate::domain::AssetType::Equity),
            open: Box::new(move || {
                MassiveReferenceConnection::open(reference_client.clone())
                    .map(|connection| Box::new(connection) as Box<dyn ReferenceDataConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_massive_equity_reference(
        mut self,
        api_key: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let client = MassiveStocksRestClient::with_base_url(api_key, base_url)
            .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?
            .for_equity();
        let reference_client = client.clone();
        self.references.push(ReferenceEntry {
            route: IntegrationRoute::data_provider("massive"),
            product: Some(ProductFamily::Equity),
            transport: TransportKind::Rest,
            asset_type: Some(crate::domain::AssetType::Equity),
            open: Box::new(move || {
                MassiveReferenceConnection::open(reference_client.clone())
                    .map(|connection| Box::new(connection) as Box<dyn ReferenceDataConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_hyperliquid_reference(mut self, endpoint: impl Into<String>) -> Self {
        let endpoint = endpoint.into();
        self.references.push(ReferenceEntry {
            route: crate::domain::IntegrationRoute::exchange("hyperliquid"),
            product: Some(ProductFamily::UsdMFutures),
            transport: TransportKind::Rest,
            asset_type: Some(crate::domain::AssetType::Crypto),
            open: Box::new(move || {
                HyperliquidReferenceConnection::open(endpoint.clone())
                    .map(|connection| Box::new(connection) as Box<dyn ReferenceDataConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn connect_reference(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn ReferenceDataConnection>, IntegrationError> {
        if spec.capability != IntegrationCapability::Reference {
            return Err(IntegrationError::InvalidRequest(
                "connection spec is not reference data".into(),
            ));
        }
        self.references
            .iter()
            .find(|entry| {
                entry.route.matches_primary(&spec.route)
                    && entry.product == spec.product
                    && entry.transport == spec.transport
                    && entry.asset_type == spec.asset_type
            })
            .ok_or(IntegrationError::UnsupportedOperation)
            .and_then(|entry| (entry.open)())
    }

    /// Adds a REST-backed market stream.  The returned connection still uses
    /// the same market-stream protocol as a future websocket implementation.
    pub fn with_binance_spot_rest_market_stream(
        mut self,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        // Validate at configuration time, while retaining only a closure in
        // the composition root.
        binance::spot::market_stream::BinanceSpotSnapshotReader::new(endpoint.clone())?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::spot::market_stream::rest_market_stream(endpoint.clone())
                    .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
            }),
        });
        Ok(self)
    }

    /// Adds Binance Spot's public WebSocket market stream.
    pub fn with_binance_spot_websocket_market_stream(
        mut self,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        binance::spot::websocket::BinanceSpotWebSocketMarketStream::new(endpoint.clone())?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::WebSocket,
            open: Box::new(move || {
                binance::spot::websocket::BinanceSpotWebSocketMarketStream::new(endpoint.clone())
                    .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_equity_market_stream(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let api_key = api_key.into();
        let secret = secret.into();
        let endpoint = endpoint.into();
        // The Stocks Trading API exposes latest quotes over REST. The
        // integration still presents stream semantics through polling.
        binance::equity::market_stream::rest_market_stream(
            api_key.clone(),
            secret.clone(),
            endpoint.clone(),
        )?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::equity::market_stream::rest_market_stream(
                    api_key.clone(),
                    secret.clone(),
                    endpoint.clone(),
                )
                .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
                .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))
            }),
        });
        Ok(self)
    }

    pub fn with_binance_derivatives_market_stream(
        mut self,
        product: ProductFamily,
        endpoint: impl Into<String>,
        path: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        let path = path.into();
        binance::derivatives_market::rest_market_stream(endpoint.clone(), path.clone(), product)?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::derivatives_market::rest_market_stream(
                    endpoint.clone(),
                    path.clone(),
                    product,
                )
                .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_okx_market_stream(
        mut self,
        product: ProductFamily,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = endpoint.into();
        crate::services::gateways::okx_market_stream::rest_market_stream(
            endpoint.clone(),
            product,
        )?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::okx_market_stream::rest_market_stream(
                    endpoint.clone(),
                    product,
                )
                .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_massive_market_stream(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let api_key = api_key.into();
        let endpoint = endpoint.into();
        crate::services::gateways::massive::market_stream::MassiveMarketStream::new(
            api_key.clone(),
            endpoint.clone(),
            product,
        )?;
        self.market_streams.push(MarketStreamEntry {
            route: crate::domain::IntegrationRoute::data_provider("massive"),
            product: Some(product),
            transport: TransportKind::WebSocket,
            open: Box::new(move || {
                crate::services::gateways::massive::market_stream::MassiveMarketStream::new(
                    api_key.clone(),
                    endpoint.clone(),
                    product,
                )
                .map(|stream| Box::new(stream) as Box<dyn MarketStreamConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_spot_account(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::spot::account::BinanceSpotAccountConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::spot::account::BinanceSpotAccountConnection::new(
                    inspection_api_key.clone(),
                    inspection_secret.clone(),
                    inspection_base_url.clone(),
                )
                .map(|connection| {
                    Box::new(connection) as Box<dyn AccountCredentialInspectionConnection>
                })
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_spot_account_market_profile(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.account_market_profiles
            .push(AccountMarketProfileEntry {
                route: crate::domain::IntegrationRoute::exchange("binance"),
                product: Some(ProductFamily::Spot),
                transport: TransportKind::Rest,
                open: Box::new(move || {
                    binance::spot::account::BinanceSpotAccountMarketProfileConnection::new(
                        api_key.clone(),
                        secret.clone(),
                        base_url.clone(),
                    )
                    .map(|connection| {
                        Box::new(connection) as Box<dyn AccountMarketProfileConnection>
                    })
                    .map_err(IntegrationError::InvalidRequest)
                }),
            });
        self
    }

    pub fn with_binance_funding_account(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: None,
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::funding::BinanceFundingAccountConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: None,
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::funding::BinanceFundingAccountConnection::new(
                    inspection_api_key.clone(),
                    inspection_secret.clone(),
                    inspection_base_url.clone(),
                )
                .map(|connection| {
                    Box::new(connection) as Box<dyn AccountCredentialInspectionConnection>
                })
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_margin_account(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin account requires cross or isolated margin".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::margin::BinanceMarginAccountConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::margin::BinanceMarginAccountConnection::new(
                    product,
                    inspection_api_key.clone(),
                    inspection_secret.clone(),
                    inspection_base_url.clone(),
                )
                .map(|connection| {
                    Box::new(connection) as Box<dyn AccountCredentialInspectionConnection>
                })
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_margin_order_entry(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin order entry requires cross or isolated margin".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::margin_order::BinanceMarginOrderConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_okx_account_market_profile(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "OKX account market profile requires spot, futures, or options product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let base_url = base_url.into();
        self.account_market_profiles
            .push(AccountMarketProfileEntry {
                route: crate::domain::IntegrationRoute::exchange("okx"),
                product: Some(product),
                transport: TransportKind::Rest,
                open: Box::new(move || {
                    let connection = OkxAccountMarketProfileConnection::new(
                        product,
                        api_key.clone(),
                        secret.clone(),
                        passphrase.clone(),
                        base_url.clone(),
                    )
                    .map_err(IntegrationError::InvalidRequest)?;
                    Ok(Box::new(connection) as Box<dyn AccountMarketProfileConnection>)
                }),
            });
        Ok(self)
    }

    pub fn with_binance_options_account(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Options),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::options::account::BinanceOptionsAccountConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Options),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::options::account::BinanceOptionsAccountConnection::new(
                    inspection_api_key.clone(),
                    inspection_secret.clone(),
                    inspection_base_url.clone(),
                )
                .map(|connection| {
                    Box::new(connection) as Box<dyn AccountCredentialInspectionConnection>
                })
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_options_order_entry(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Options),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::options::order::BinanceOptionsOrderConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_spot_order_entry(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::spot::order::BinanceSpotOrderConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_equity_order_entry(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::equity::order::BinanceEquityOrderConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_equity_order_query(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_queries.push(OrderQueryEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::equity::query::BinanceEquityOrderQueryConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderQueryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_order_query(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_queries.push(OrderQueryEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::order_query::BinanceOrderQueryConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderQueryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_okx_order_query(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let base_url = base_url.into();
        self.order_queries.push(OrderQueryEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::okx::OkxOrderQueryConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    passphrase.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderQueryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_ibkr_order_entry(
        mut self,
        host: impl Into<String>,
        port: u16,
        client_id: i32,
    ) -> Self {
        let host = host.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::ibkr::IbkrOptions::new(host.clone(), port, client_id)
                    .and_then(crate::services::gateways::ibkr::IbkrOrderConnection::new)
                    .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_ibkr_account(mut self, host: impl Into<String>, port: u16, client_id: i32) -> Self {
        let host = host.into();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::ibkr::IbkrOptions::new(host.clone(), port, client_id)
                    .and_then(crate::services::gateways::ibkr::IbkrAccountConnection::new)
                    .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_ibkr_account_stream(
        mut self,
        host: impl Into<String>,
        port: u16,
        client_id: i32,
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Self {
        let host = host.into();
        let account_id = account_id.into();
        let segment_key = segment_key.into();
        self.account_streams.push(AccountStreamEntry {
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::ibkr::IbkrOptions::new(host.clone(), port, client_id)
                    .and_then(|options| {
                        crate::services::gateways::ibkr::IbkrAccountStreamConnection::new(
                            options,
                            account_id.clone(),
                            segment_key.clone(),
                        )
                    })
                    .map(|connection| Box::new(connection) as Box<dyn AccountEventStreamConnection>)
                    .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_ibkr_execution_stream(
        mut self,
        host: impl Into<String>,
        port: u16,
        client_id: i32,
        account_id: impl Into<String>,
        symbol: Option<String>,
    ) -> Self {
        let host = host.into();
        let account_id = account_id.into();
        self.execution_streams.push(ExecutionStreamEntry {
            route: crate::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                crate::services::gateways::ibkr::IbkrExecutionStreamConnection::new(
                    crate::services::gateways::ibkr::IbkrOptions::new(
                        host.clone(),
                        port,
                        client_id,
                    )
                    .map_err(IntegrationError::InvalidRequest)?,
                    account_id.clone(),
                    symbol.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn ExecutionStreamConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_spot_account_stream(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let websocket_endpoint = websocket_endpoint.into();
        let segment_key = segment_key.into();
        self.account_streams.push(AccountStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            transport: TransportKind::UserStream,
            open: Box::new(move || {
                binance::spot::user_stream::BinanceSpotAccountStreamConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                    websocket_endpoint.clone(),
                    segment_key.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountEventStreamConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_margin_account_stream(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance margin account stream requires cross or isolated margin".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let websocket_endpoint = websocket_endpoint.into();
        let segment_key = segment_key.into();
        self.account_streams.push(AccountStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::UserStream,
            open: Box::new(move || {
                binance::spot::user_stream::BinanceSpotAccountStreamConnection::new_for_product(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                    websocket_endpoint.clone(),
                    segment_key.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountEventStreamConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_okx_account_stream(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "OKX account stream requires spot, margin, futures, or options product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let websocket_endpoint = websocket_endpoint.into();
        let segment_key = segment_key.into();
        self.account_streams.push(AccountStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::UserStream,
            open: Box::new(move || {
                Ok(Box::new(
                    OkxAccountStreamConnection::new(
                        product,
                        api_key.clone(),
                        secret.clone(),
                        passphrase.clone(),
                        websocket_endpoint.clone(),
                        segment_key.clone(),
                    )
                    .map_err(IntegrationError::InvalidRequest)?,
                ) as Box<dyn AccountEventStreamConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_futures_account_stream(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
        websocket_endpoint: impl Into<String>,
        segment_key: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance futures account stream requires USD-M or Coin-M".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let websocket_endpoint = websocket_endpoint.into();
        let segment_key = segment_key.into();
        self.account_streams.push(AccountStreamEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::UserStream,
            open: Box::new(move || {
                binance::futures::account::BinanceFuturesAccountStreamConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                    websocket_endpoint.clone(),
                    segment_key.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountEventStreamConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_earn(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.earns.push(EarnEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::earn::BinanceSimpleEarnConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn EarnConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    pub fn with_binance_transfer(
        mut self,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.transfers.push(TransferEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::transfer::BinanceTransferConnection::new(
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn TransferConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self
    }

    /// Adds a Binance USD-M or Coin-M futures private account connection.
    /// The provider-specific REST client remains behind the account-read
    /// application protocol, just like the Spot connection.
    pub fn with_binance_futures_account(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance futures account requires USD-M or Coin-M product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::futures::account::BinanceFuturesAccountConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn AccountReadConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::futures::account::BinanceFuturesAccountConnection::new(
                    product,
                    inspection_api_key.clone(),
                    inspection_secret.clone(),
                    inspection_base_url.clone(),
                )
                .map(|connection| {
                    Box::new(connection) as Box<dyn AccountCredentialInspectionConnection>
                })
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_binance_futures_order_entry(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::UsdMFutures | ProductFamily::CoinMFutures
        ) {
            return Err(IntegrationError::InvalidRequest(
                "Binance futures order entry requires USD-M or Coin-M product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                binance::futures::order::BinanceFuturesOrderConnection::new(
                    product,
                    api_key.clone(),
                    secret.clone(),
                    base_url.clone(),
                )
                .map(|connection| Box::new(connection) as Box<dyn OrderEntryConnection>)
                .map_err(IntegrationError::InvalidRequest)
            }),
        });
        Ok(self)
    }

    pub fn with_okx_account(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "OKX account requires spot, futures, or options product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let base_url = base_url.into();
        let inspection_api_key = api_key.clone();
        let inspection_secret = secret.clone();
        let inspection_passphrase = passphrase.clone();
        let inspection_base_url = base_url.clone();
        self.accounts.push(AccountEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                Ok(Box::new(
                    OkxAccountConnection::new(
                        product,
                        api_key.clone(),
                        secret.clone(),
                        passphrase.clone(),
                        base_url.clone(),
                    )
                    .map_err(IntegrationError::InvalidRequest)?,
                ) as Box<dyn AccountReadConnection>)
            }),
        });
        self.credential_inspections.push(CredentialInspectionEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                Ok(Box::new(
                    OkxAccountConnection::new(
                        product,
                        inspection_api_key.clone(),
                        inspection_secret.clone(),
                        inspection_passphrase.clone(),
                        inspection_base_url.clone(),
                    )
                    .map_err(IntegrationError::InvalidRequest)?,
                )
                    as Box<dyn AccountCredentialInspectionConnection>)
            }),
        });
        Ok(self)
    }

    pub fn with_okx_order_entry(
        mut self,
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        passphrase: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        if !matches!(
            product,
            ProductFamily::Spot
                | ProductFamily::CrossMargin
                | ProductFamily::IsolatedMargin
                | ProductFamily::UsdMFutures
                | ProductFamily::CoinMFutures
                | ProductFamily::Options
        ) {
            return Err(IntegrationError::InvalidRequest(
                "OKX order entry requires spot, margin, futures, or options product".into(),
            ));
        }
        let api_key = api_key.into();
        let secret = secret.into();
        let passphrase = passphrase.into();
        let base_url = base_url.into();
        self.order_entries.push(OrderEntry {
            route: crate::domain::IntegrationRoute::exchange("okx"),
            product: Some(product),
            transport: TransportKind::Rest,
            open: Box::new(move || {
                Ok(Box::new(
                    OkxOrderConnection::new(
                        product,
                        api_key.clone(),
                        secret.clone(),
                        passphrase.clone(),
                        base_url.clone(),
                    )
                    .map_err(IntegrationError::InvalidRequest)?,
                ) as Box<dyn OrderEntryConnection>)
            }),
        });
        Ok(self)
    }
}

impl Default for Integration {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::Integration;
    use crate::application::ConnectionSpec;
    use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};

    fn reference_spec() -> ConnectionSpec {
        ConnectionSpec {
            connection_id: "reference.binance.spot.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        }
    }

    #[test]
    fn one_integration_facade_creates_multiple_opaque_connections() {
        let integration = Integration::new()
            .with_binance_reference("https://example.test")
            .unwrap();
        let mut first = integration.connect(&reference_spec()).unwrap();
        let mut second = integration
            .connect(&ConnectionSpec {
                connection_id: "reference.binance.spot.rest.second".into(),
                ..reference_spec()
            })
            .unwrap();
        assert_eq!(first.identity().route.primary().id, "binance");
        assert_eq!(second.identity().route.primary().id, "binance");
        first.start().unwrap();
        second.start().unwrap();
        assert!(first.health().healthy);
        assert!(second.health().healthy);
    }

    #[test]
    fn websocket_market_stream_is_selected_without_opening_network_during_composition() {
        let integration = Integration::new()
            .with_binance_spot_websocket_market_stream("wss://stream.binance.com:9443/ws")
            .unwrap();
        let connection = integration
            .connect_market_stream(&ConnectionSpec {
                connection_id: "market.binance.spot.websocket".into(),
                route: crate::domain::IntegrationRoute::exchange("binance"),
                product: Some(ProductFamily::Spot),
                access: AccessScope::Public,
                transport: TransportKind::WebSocket,
                capability: IntegrationCapability::MarketStream,
                credential_id: None,
                asset_type: None,
            })
            .unwrap();
        assert_eq!(connection.identity().transport, TransportKind::WebSocket);
    }

    #[test]
    fn one_integration_facade_composes_every_required_market_product() {
        let integration = Integration::new()
            .with_binance_spot_rest_market_stream("https://example.test")
            .unwrap()
            .with_binance_derivatives_market_stream(
                ProductFamily::UsdMFutures,
                "https://example.test",
                "/fapi/v1/ticker/bookTicker",
            )
            .unwrap()
            .with_binance_derivatives_market_stream(
                ProductFamily::CoinMFutures,
                "https://example.test",
                "/dapi/v1/ticker/bookTicker",
            )
            .unwrap()
            .with_binance_derivatives_market_stream(
                ProductFamily::Options,
                "https://example.test",
                "/eapi/v1/ticker",
            )
            .unwrap()
            .with_binance_equity_market_stream("key", "secret", "https://example.test")
            .unwrap()
            .with_okx_market_stream(ProductFamily::Spot, "https://example.test")
            .unwrap()
            .with_okx_market_stream(ProductFamily::UsdMFutures, "https://example.test")
            .unwrap()
            .with_okx_market_stream(ProductFamily::CoinMFutures, "https://example.test")
            .unwrap()
            .with_okx_market_stream(ProductFamily::Options, "https://example.test")
            .unwrap()
            .with_massive_market_stream(
                ProductFamily::Equity,
                "massive-key",
                "wss://example.test/stocks",
            )
            .unwrap()
            .with_massive_market_stream(
                ProductFamily::Options,
                "massive-key",
                "wss://example.test/options",
            )
            .unwrap();

        let products = [
            ("binance", ProductFamily::Spot),
            ("binance", ProductFamily::UsdMFutures),
            ("binance", ProductFamily::CoinMFutures),
            ("binance", ProductFamily::Options),
            ("binance", ProductFamily::Equity),
            ("okx", ProductFamily::Spot),
            ("okx", ProductFamily::UsdMFutures),
            ("okx", ProductFamily::CoinMFutures),
            ("okx", ProductFamily::Options),
            ("massive", ProductFamily::Equity),
            ("massive", ProductFamily::Options),
        ];
        assert_eq!(products.len(), 11);
        for (index, (provider, product)) in products.into_iter().enumerate() {
            let connection = integration
                .connect_market_stream(&ConnectionSpec {
                    connection_id: format!("market-{index}"),
                    route: if provider == "massive" {
                        crate::domain::IntegrationRoute::data_provider(provider)
                    } else {
                        crate::domain::IntegrationRoute::exchange(provider)
                    },
                    product: Some(product),
                    access: AccessScope::Public,
                    transport: if provider == "massive" {
                        TransportKind::WebSocket
                    } else {
                        TransportKind::Rest
                    },
                    capability: IntegrationCapability::MarketStream,
                    credential_id: None,
                    asset_type: None,
                })
                .unwrap();
            assert_eq!(connection.identity().route.primary().id, provider);
            assert_eq!(connection.identity().product, Some(product));
        }
    }
}
