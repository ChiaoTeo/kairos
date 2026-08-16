//! Binance participant-native connection facade.

mod account;
pub mod blocking;
mod execution;
mod funding;
mod reference;

pub use account::{
    BinanceFundingAccountRead, BinanceFundingCredentialInspection, BinanceFuturesAccountEvents,
    BinanceFuturesAccountRead, BinanceFuturesCredentialInspection, BinanceMarginAccountEvents,
    BinanceMarginAccountRead, BinanceMarginCredentialInspection, BinanceOptionsAccountEvents,
    BinanceOptionsAccountRead, BinanceOptionsCredentialInspection, BinanceSpotAccountEvents,
    BinanceSpotAccountMarketProfile, BinanceSpotAccountRead, BinanceSpotCredentialInspection,
};
pub use execution::{
    BinanceFuturesOrderEntry, BinanceFuturesOrderEvents, BinanceFuturesOrderQuery,
    BinanceMarginOrderEntry, BinanceMarginOrderEvents, BinanceMarginOrderQuery,
    BinanceOptionsOrderEntry, BinanceOptionsOrderEvents, BinanceOptionsOrderQuery,
    BinanceSpotOrderEntry, BinanceSpotOrderEvents, BinanceSpotOrderQuery,
};
pub use funding::{BinanceSimpleEarn, BinanceTransfer};
pub use reference::{
    BinanceEquityInstrumentCatalog, BinanceEquityMarketQuote, BinanceInstrumentCatalog,
};

use secrecy::{ExposeSecret, SecretString};
use std::sync::Arc;

use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrumentCatalog,
};
use crate::application::capabilities::{
    OrderEntryEvent, OrderEntryRequest, ParticipantKind, ParticipantRef,
};
use crate::application::{
    AsyncAccountCredentialInspectionConnection, AsyncAccountEventSource,
    AsyncAccountMarketProfileConnection, AsyncAccountReadConnection, AsyncEarnConnection,
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection,
    AsyncTransferConnection, CommandResult, ConnectionDescriptor, EarnActionResult, EarnPosition,
    EarnProduct, EarnProductType, EarnRedeemRequest, EarnReward, EarnSubscribeRequest,
    ExternalAccountCredentialProfile, ExternalMarketProfile, ExternalMarketProfileRequest,
    ExternalOrder, ExternalOrderQuery, IntegrationError, TransferRequest, TransferResult,
};
use crate::services::participants::binance::async_margin_account_events::BinanceAsyncMarginAccountEventSource;
use crate::services::participants::binance::async_margin_order_events::BinanceAsyncMarginOrderEventSource;
use crate::services::participants::binance::clock::BinanceServerClock;
use crate::services::participants::binance::futures::{
    account::BinanceFuturesAccountClient,
    async_account_events::BinanceFuturesAsyncAccountEventSource,
    async_order_events::BinanceFuturesAsyncOrderEventSource, order::BinanceFuturesOrderConnection,
};
use crate::services::participants::binance::margin_order::BinanceMarginOrderConnection;
use crate::services::participants::binance::margin_query::BinanceMarginOrderQueryConnection;
use crate::services::participants::binance::options::{
    account::BinanceOptionsAccountClient,
    async_account_events::BinanceOptionsAsyncAccountEventSource,
    async_order_events::BinanceOptionsAsyncOrderEventSource, order::BinanceOptionsOrderConnection,
};
use crate::services::participants::binance::order_query::BinanceOrderQueryConnection;
use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;
use crate::services::participants::binance::spot::account::{
    normalize_account, normalize_market_profile,
};
use crate::services::participants::binance::spot::async_account_events::BinanceSpotAsyncAccountEventSource;
use crate::services::participants::binance::spot::async_order_events::BinanceSpotAsyncOrderEventSource;
use crate::services::participants::binance::spot::order::BinanceSpotOrderConnection;
use crate::services::participants::binance::spot::order_events::BinanceSpotOrderEventSource;
use crate::services::participants::binance::spot::runtime::{
    BinanceRequestRuntime, PrincipalOrderQuota, QuotaAllocation, RequestPriority,
};
use crate::services::participants::binance::{
    equity as equity_catalog,
    funding::{account as funding_account, earn, transfer},
    market_data::catalog as instrument_catalog,
};
use crate::services::quota::SharedFixedWindowQuota;
use crate::services::transport::http::PublicHttpClient;

use super::config::{
    BinanceFuturesChannelConfig, BinanceFuturesConnectionConfig, BinanceMarginChannelConfig,
    BinanceOptionsChannelConfig, BinanceOptionsConnectionConfig, BinancePrincipalConfig,
    BinanceSpotChannelConfig, BinanceSpotConnectionConfig,
};
use super::types::{ConnectionDomain, InstrumentType};

/// Binance Spot API-family connection. Margin and funding projections are
/// available only from this family because they use the Spot REST/auth scope.
pub struct BinanceSpotConnection {
    config: BinanceConnectionParts,
    runtime: BinanceRequestRuntime,
    clock: BinanceServerClock,
}

/// Binance USD-M Futures API-family connection.
pub struct BinanceUsdMConnection {
    config: BinanceConnectionParts,
    runtime: BinanceRequestRuntime,
}

/// Binance COIN-M Futures API-family connection.
pub struct BinanceCoinMConnection {
    config: BinanceConnectionParts,
    runtime: BinanceRequestRuntime,
}

/// Binance Options API-family connection.
pub struct BinanceOptionsConnection {
    config: BinanceConnectionParts,
    runtime: BinanceRequestRuntime,
}

/// One provider/principal context. Capability handles projected from this
/// object share the same authenticated HTTP client and worker.
pub struct BinanceSpotPrincipalConnection {
    binding_id: String,
    participant: ParticipantRef,
    environment: String,
    principal_id: Option<String>,
    client: BinanceSpotAccountClient,
}

pub struct BinanceFuturesPrincipalConnection {
    descriptor: ConnectionDescriptor,
    client: BinanceFuturesAccountClient,
    domain: ConnectionDomain,
}

pub struct BinanceMarginPrincipalConnection {
    descriptor: ConnectionDescriptor,
    client: BinanceSpotAccountClient,
    domain: ConnectionDomain,
    isolated_symbol: Option<String>,
}

pub struct BinanceOptionsPrincipalConnection {
    descriptor: ConnectionDescriptor,
    client: BinanceOptionsAccountClient,
}

#[derive(Clone, Debug)]
struct BinanceConnectionParts {
    environment: String,
    rest_base_url: String,
    quota: super::config::BinanceQuotaAllocation,
    shared_quota: Option<super::config::BinanceSharedQuotaConfig>,
}

impl From<BinanceSpotConnectionConfig> for BinanceConnectionParts {
    fn from(value: BinanceSpotConnectionConfig) -> Self {
        Self {
            environment: value.environment,
            rest_base_url: value.rest_base_url,
            quota: value.quota,
            shared_quota: value.shared_quota,
        }
    }
}

impl From<BinanceFuturesConnectionConfig> for BinanceConnectionParts {
    fn from(value: BinanceFuturesConnectionConfig) -> Self {
        Self {
            environment: value.environment,
            rest_base_url: value.rest_base_url,
            quota: value.quota,
            shared_quota: value.shared_quota,
        }
    }
}

impl From<BinanceOptionsConnectionConfig> for BinanceConnectionParts {
    fn from(value: BinanceOptionsConnectionConfig) -> Self {
        Self {
            environment: value.environment,
            rest_base_url: value.rest_base_url,
            quota: value.quota,
            shared_quota: value.shared_quota,
        }
    }
}

fn connect_family(
    mut config: BinanceConnectionParts,
    api_family: &str,
) -> Result<(BinanceConnectionParts, BinanceRequestRuntime), IntegrationError> {
    config.environment = config.environment.trim().to_string();
    config.rest_base_url = config.rest_base_url.trim_end_matches('/').to_string();
    if config.environment.is_empty() {
        return Err(IntegrationError::InvalidRequest(
            "Binance environment is required".into(),
        ));
    }
    if !(config.rest_base_url.starts_with("https://")
        || config.rest_base_url.starts_with("http://"))
    {
        return Err(IntegrationError::InvalidRequest(
            "Binance REST endpoint must start with http:// or https://".into(),
        ));
    }
    let http = PublicHttpClient::new("kairos-integration/binance")
        .map_err(|error| IntegrationError::Transport(error.to_string()))?;
    let shared_quota = config
        .shared_quota
        .as_ref()
        .map(|shared| {
            if shared.egress_scope_id.trim().is_empty() {
                return Err(IntegrationError::InvalidRequest(
                    "Binance egress scope id is required".into(),
                ));
            }
            SharedFixedWindowQuota::open_or_register(
                &shared.ledger_path,
                &format!(
                    "binance:{}:{}:egress:{}:request-weight-1m",
                    config.environment,
                    api_family,
                    shared.egress_scope_id.trim()
                ),
                config.quota.request_weight_per_minute,
                config.quota.cancel_reserve_weight,
                60_000,
            )
            .map_err(|error| {
                IntegrationError::Unavailable(format!("open Binance shared quota ledger: {error}"))
            })
        })
        .transpose()?;
    let runtime = BinanceRequestRuntime::new_with_shared_quota(
        http,
        QuotaAllocation {
            request_weight_per_minute: config.quota.request_weight_per_minute,
            cancel_reserve_weight: config.quota.cancel_reserve_weight,
        },
        shared_quota,
    )
    .map_err(map_exchange_error)?;
    Ok((config, runtime))
}

impl BinanceSpotConnection {
    pub fn connect(config: BinanceSpotConnectionConfig) -> Result<Self, IntegrationError> {
        let (config, runtime) = connect_family(config.into(), "spot")?;
        Ok(Self {
            config,
            runtime,
            clock: BinanceServerClock::default(),
        })
    }

    pub fn instrument_catalog(&self) -> BinanceInstrumentCatalog {
        BinanceInstrumentCatalog {
            descriptor: public_descriptor(&self.config.environment, InstrumentType::Spot),
            instrument_type: InstrumentType::Spot,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }

    pub fn blocking_instrument_catalog(&self) -> blocking::BinanceInstrumentCatalog {
        blocking::BinanceInstrumentCatalog {
            descriptor: public_descriptor(&self.config.environment, InstrumentType::Spot),
            instrument_type: InstrumentType::Spot,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }

    pub fn principal_connection(
        &self,
        config: BinancePrincipalConfig,
    ) -> Result<BinanceSpotPrincipalConnection, IntegrationError> {
        spot_principal_connection(self, config)
    }

    pub fn equity_instrument_catalog(
        &self,
        api_key: SecretString,
    ) -> BinanceEquityInstrumentCatalog {
        BinanceEquityInstrumentCatalog {
            descriptor: ConnectionDescriptor {
                binding_id: "binance.equity.catalog".into(),
                participant: ParticipantRef::new(ParticipantKind::Broker, "binance")
                    .expect("static Binance broker participant"),
                environment: self.config.environment.clone(),
                principal_id: None,
                domain: crate::application::ConnectionDomainRef::new("equity")
                    .expect("static Binance equity domain"),
            },
            base_url: self.config.rest_base_url.clone(),
            api_key,
            runtime: self.runtime.clone(),
        }
    }

    pub fn equity_market_quote(&self, api_key: SecretString) -> BinanceEquityMarketQuote {
        BinanceEquityMarketQuote {
            descriptor: ConnectionDescriptor {
                binding_id: "binance.equity.market.quote".into(),
                participant: ParticipantRef::new(ParticipantKind::Broker, "binance")
                    .expect("static Binance broker participant"),
                environment: self.config.environment.clone(),
                principal_id: None,
                domain: crate::application::ConnectionDomainRef::new("equity")
                    .expect("static Binance equity domain"),
            },
            base_url: self.config.rest_base_url.clone(),
            api_key,
            runtime: self.runtime.clone(),
        }
    }
}

impl BinanceUsdMConnection {
    pub fn connect(config: BinanceFuturesConnectionConfig) -> Result<Self, IntegrationError> {
        let (config, runtime) = connect_family(config.into(), "usd-m-futures")?;
        Ok(Self { config, runtime })
    }

    pub fn principal_connection(
        &self,
        config: BinancePrincipalConfig,
    ) -> Result<BinanceFuturesPrincipalConnection, IntegrationError> {
        futures_principal_connection(
            &self.config,
            &self.runtime,
            config,
            ConnectionDomain::UsdMFutures,
        )
    }

    pub fn instrument_catalog(&self) -> BinanceInstrumentCatalog {
        BinanceInstrumentCatalog {
            descriptor: public_descriptor(&self.config.environment, InstrumentType::UsdMFutures),
            instrument_type: InstrumentType::UsdMFutures,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }
}

impl BinanceCoinMConnection {
    pub fn connect(config: BinanceFuturesConnectionConfig) -> Result<Self, IntegrationError> {
        let (config, runtime) = connect_family(config.into(), "coin-m-futures")?;
        Ok(Self { config, runtime })
    }

    pub fn principal_connection(
        &self,
        config: BinancePrincipalConfig,
    ) -> Result<BinanceFuturesPrincipalConnection, IntegrationError> {
        futures_principal_connection(
            &self.config,
            &self.runtime,
            config,
            ConnectionDomain::CoinMFutures,
        )
    }

    pub fn instrument_catalog(&self) -> BinanceInstrumentCatalog {
        BinanceInstrumentCatalog {
            descriptor: public_descriptor(&self.config.environment, InstrumentType::CoinMFutures),
            instrument_type: InstrumentType::CoinMFutures,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }
}

impl BinanceOptionsConnection {
    pub fn connect(config: BinanceOptionsConnectionConfig) -> Result<Self, IntegrationError> {
        let (config, runtime) = connect_family(config.into(), "options")?;
        Ok(Self { config, runtime })
    }

    pub fn principal_connection(
        &self,
        config: BinancePrincipalConfig,
    ) -> Result<BinanceOptionsPrincipalConnection, IntegrationError> {
        if config.principal_quota.is_some() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Options principal does not accept Spot order quota".into(),
            ));
        }
        let client = BinanceOptionsAccountClient::from_runtime(
            self.runtime.clone(),
            config.api_key.expose_secret().to_owned(),
            config.secret.expose_secret().to_owned(),
            self.config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        Ok(BinanceOptionsPrincipalConnection {
            descriptor: family_descriptor(
                config.binding_id,
                self.config.environment.clone(),
                config.principal_id,
                ConnectionDomain::Options,
            ),
            client,
        })
    }

    pub fn instrument_catalog(&self) -> BinanceInstrumentCatalog {
        BinanceInstrumentCatalog {
            descriptor: public_descriptor(&self.config.environment, InstrumentType::Option),
            instrument_type: InstrumentType::Option,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }
}

fn futures_principal_connection(
    config: &BinanceConnectionParts,
    runtime: &BinanceRequestRuntime,
    principal: BinancePrincipalConfig,
    domain: ConnectionDomain,
) -> Result<BinanceFuturesPrincipalConnection, IntegrationError> {
    if principal.principal_quota.is_some() {
        return Err(IntegrationError::InvalidRequest(
            "Binance Futures principal does not accept Spot order quota".into(),
        ));
    }
    let client = BinanceFuturesAccountClient::from_runtime(
        runtime.clone(),
        domain,
        principal.api_key.expose_secret().to_owned(),
        principal.secret.expose_secret().to_owned(),
        config.rest_base_url.clone(),
    )
    .map_err(map_exchange_error)?;
    Ok(BinanceFuturesPrincipalConnection {
        descriptor: family_descriptor(
            principal.binding_id,
            config.environment.clone(),
            principal.principal_id,
            domain,
        ),
        client,
        domain,
    })
}

fn family_descriptor(
    binding_id: String,
    environment: String,
    principal_id: Option<String>,
    domain: ConnectionDomain,
) -> ConnectionDescriptor {
    ConnectionDescriptor {
        binding_id: format!("{}.{}", binding_id, domain.as_str()),
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        environment,
        principal_id,
        domain: domain.into(),
    }
}

fn public_descriptor(environment: &str, instrument_type: InstrumentType) -> ConnectionDescriptor {
    let domain = match instrument_type {
        InstrumentType::Spot => ConnectionDomain::Spot,
        InstrumentType::UsdMFutures => ConnectionDomain::UsdMFutures,
        InstrumentType::CoinMFutures => ConnectionDomain::CoinMFutures,
        InstrumentType::Option => ConnectionDomain::Options,
    };
    ConnectionDescriptor {
        binding_id: format!("binance.public.{}", domain.as_str()),
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        environment: environment.to_owned(),
        principal_id: None,
        domain: domain.into(),
    }
}

fn spot_principal_connection(
    owner: &BinanceSpotConnection,
    config: BinancePrincipalConfig,
) -> Result<BinanceSpotPrincipalConnection, IntegrationError> {
    let binding_id = config.binding_id.trim().to_owned();
    if binding_id.is_empty() {
        return Err(IntegrationError::InvalidRequest(
            "Binance principal binding id is required".into(),
        ));
    }
    if config
        .principal_id
        .as_ref()
        .is_some_and(|value| value.trim().is_empty())
    {
        return Err(IntegrationError::InvalidRequest(
            "Binance principal id cannot be empty".into(),
        ));
    }
    let runtime = match config.principal_quota {
        Some(allocation) => {
            let shared = owner.config.shared_quota.as_ref().ok_or_else(|| {
                IntegrationError::InvalidRequest(
                    "Binance principal quota requires a shared quota ledger".into(),
                )
            })?;
            let principal_id = config.principal_id.as_deref().ok_or_else(|| {
                IntegrationError::InvalidRequest(
                    "Binance principal quota requires principal_id".into(),
                )
            })?;
            if allocation.orders_per_10_seconds == 0 || allocation.orders_per_day == 0 {
                return Err(IntegrationError::InvalidRequest(
                    "Binance principal order quota limits must be positive".into(),
                ));
            }
            let quota = |rate_limit_id: &str, limit: u32, window_millis: u64| {
                SharedFixedWindowQuota::open_or_register(
                    &shared.ledger_path,
                    &format!(
                        "binance:{}:spot:egress:{}:principal:{}:{rate_limit_id}",
                        owner.config.environment,
                        shared.egress_scope_id.trim(),
                        principal_id,
                    ),
                    limit,
                    0,
                    window_millis,
                )
                .map(Arc::new)
                .map_err(|error| {
                    IntegrationError::Unavailable(format!(
                        "open Binance Spot principal quota slot: {error}"
                    ))
                })
            };
            owner.runtime.with_principal_order_quotas(vec![
                PrincipalOrderQuota {
                    header_name: "x-mbx-order-count-10s",
                    quota: quota(
                        "unfilled-orders-10s",
                        allocation.orders_per_10_seconds,
                        10_000,
                    )?,
                },
                PrincipalOrderQuota {
                    header_name: "x-mbx-order-count-1d",
                    quota: quota("unfilled-orders-1d", allocation.orders_per_day, 86_400_000)?,
                },
            ])
        }
        None => owner.runtime.clone(),
    };
    let api_key = config.api_key.expose_secret().to_owned();
    let secret = config.secret.expose_secret().to_owned();
    let client = BinanceSpotAccountClient::from_runtime_with_clock(
        runtime.clone(),
        owner.clock.clone(),
        api_key.clone(),
        secret.clone(),
        owner.config.rest_base_url.clone(),
    )
    .map_err(map_exchange_error)?;
    Ok(BinanceSpotPrincipalConnection {
        binding_id,
        participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
            .expect("static Binance participant"),
        environment: owner.config.environment.clone(),
        principal_id: config.principal_id,
        client,
    })
}

fn map_exchange_error(error: crate::services::transport::http::ExchangeError) -> IntegrationError {
    match error {
        crate::services::transport::http::ExchangeError::Authentication(message) => {
            IntegrationError::Authentication(message)
        }
        crate::services::transport::http::ExchangeError::InvalidRequest(message) => {
            IntegrationError::InvalidRequest(message)
        }
        crate::services::transport::http::ExchangeError::LocalRateLimit { message, .. } => {
            IntegrationError::RateLimited(message)
        }
        other => IntegrationError::Transport(other.to_string()),
    }
}

impl BinanceSpotPrincipalConnection {
    pub fn spot_descriptor(&self) -> ConnectionDescriptor {
        self.domain_descriptor(ConnectionDomain::Spot)
    }

    pub fn cross_margin_connection(&self) -> BinanceMarginPrincipalConnection {
        BinanceMarginPrincipalConnection {
            descriptor: self.domain_descriptor(ConnectionDomain::CrossMargin),
            client: self.client.clone(),
            domain: ConnectionDomain::CrossMargin,
            isolated_symbol: None,
        }
    }

    pub fn isolated_margin_connection(
        &self,
        symbol: impl Into<String>,
    ) -> Result<BinanceMarginPrincipalConnection, IntegrationError> {
        let symbol = symbol.into().trim().to_ascii_uppercase();
        if symbol.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "isolated-margin connection requires a provider symbol".into(),
            ));
        }
        Ok(BinanceMarginPrincipalConnection {
            descriptor: self.domain_descriptor(ConnectionDomain::IsolatedMargin),
            client: self.client.clone(),
            domain: ConnectionDomain::IsolatedMargin,
            isolated_symbol: Some(symbol),
        })
    }

    fn domain_descriptor(&self, domain: ConnectionDomain) -> ConnectionDescriptor {
        ConnectionDescriptor {
            binding_id: if domain == ConnectionDomain::Spot {
                self.binding_id.clone()
            } else {
                format!("{}.{}", self.binding_id, domain.as_str())
            },
            participant: self.participant.clone(),
            environment: self.environment.clone(),
            principal_id: self.principal_id.clone(),
            domain: domain.into(),
        }
    }

    fn spot_channel(
        config: &BinanceSpotChannelConfig,
    ) -> Result<(String, usize), IntegrationError> {
        let endpoint = config.websocket_api_url.trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("wss://") || endpoint.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot WebSocket API endpoint must start with ws:// or wss://".into(),
            ));
        }
        if config.event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "Binance Spot event queue capacity must be positive".into(),
            ));
        }
        Ok((endpoint, config.event_queue_capacity))
    }

    /// Atomically replace the principal credential used by all capability
    /// handles projected from this connection. Existing private channels
    /// detect the generation change and require a reconnect before delivering
    /// more events.
    pub fn rotate_credentials(
        &self,
        api_key: SecretString,
        secret: SecretString,
    ) -> Result<u64, IntegrationError> {
        self.client
            .rotate_credentials(
                api_key.expose_secret().to_owned(),
                secret.expose_secret().to_owned(),
            )
            .map_err(map_exchange_error)
    }

    pub fn spot_order_entry(&self) -> Result<BinanceSpotOrderEntry, IntegrationError> {
        BinanceSpotOrderConnection::from_client(self.client.clone())
            .map(|inner| BinanceSpotOrderEntry { inner })
            .map_err(IntegrationError::InvalidRequest)
    }

    pub fn spot_order_query(&self) -> Result<BinanceSpotOrderQuery, IntegrationError> {
        BinanceOrderQueryConnection::spot_from_client(self.client.clone())
            .map(|inner| BinanceSpotOrderQuery { inner })
            .map_err(IntegrationError::InvalidRequest)
    }

    pub fn blocking_spot_order_entry(
        &self,
    ) -> Result<blocking::BinanceSpotOrderEntry, IntegrationError> {
        BinanceSpotOrderConnection::from_client(self.client.clone())
            .map(|inner| blocking::BinanceSpotOrderEntry { inner })
            .map_err(IntegrationError::InvalidRequest)
    }

    pub fn blocking_spot_order_query(
        &self,
    ) -> Result<blocking::BinanceSpotOrderQuery, IntegrationError> {
        BinanceOrderQueryConnection::spot_from_client(self.client.clone())
            .map(|inner| blocking::BinanceSpotOrderQuery { inner })
            .map_err(IntegrationError::InvalidRequest)
    }

    pub fn spot_order_events(
        &self,
        config: &BinanceSpotChannelConfig,
    ) -> Result<BinanceSpotOrderEvents, IntegrationError> {
        let (endpoint, capacity) = Self::spot_channel(config)?;
        BinanceSpotAsyncOrderEventSource::from_client_with_capacity(
            self.binding_id.clone(),
            self.client.clone(),
            endpoint,
            capacity,
        )
        .map(|inner| BinanceSpotOrderEvents { inner })
    }

    pub fn spot_account_events(
        &self,
        segment_key: impl Into<String>,
        config: &BinanceSpotChannelConfig,
    ) -> Result<BinanceSpotAccountEvents, IntegrationError> {
        let (endpoint, capacity) = Self::spot_channel(config)?;
        BinanceSpotAsyncAccountEventSource::from_client_with_capacity(
            format!("{}.account-events", self.binding_id),
            segment_key,
            self.client.clone(),
            endpoint,
            capacity,
        )
        .map(|inner| BinanceSpotAccountEvents { inner })
    }

    pub fn spot_account_read(&self) -> BinanceSpotAccountRead {
        BinanceSpotAccountRead {
            client: self.client.clone(),
        }
    }

    pub fn spot_credential_inspection(&self) -> BinanceSpotCredentialInspection {
        BinanceSpotCredentialInspection {
            descriptor: self.spot_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn spot_account_market_profile(&self) -> BinanceSpotAccountMarketProfile {
        BinanceSpotAccountMarketProfile {
            client: self.client.clone(),
        }
    }

    pub fn funding_account_read(&self) -> BinanceFundingAccountRead {
        BinanceFundingAccountRead {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn funding_credential_inspection(&self) -> BinanceFundingCredentialInspection {
        BinanceFundingCredentialInspection {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn earn(&self) -> BinanceSimpleEarn {
        BinanceSimpleEarn {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn transfer(&self) -> BinanceTransfer {
        BinanceTransfer {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn blocking_funding_account_read(&self) -> blocking::BinanceFundingAccountRead {
        blocking::BinanceFundingAccountRead {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn blocking_funding_credential_inspection(
        &self,
    ) -> blocking::BinanceFundingCredentialInspection {
        blocking::BinanceFundingCredentialInspection {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn blocking_earn(&self) -> blocking::BinanceSimpleEarn {
        blocking::BinanceSimpleEarn {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    pub fn blocking_transfer(&self) -> blocking::BinanceTransfer {
        blocking::BinanceTransfer {
            descriptor: self.funding_descriptor(),
            client: self.client.clone(),
        }
    }

    fn funding_descriptor(&self) -> ConnectionDescriptor {
        self.domain_descriptor(ConnectionDomain::Funding)
    }

    pub fn blocking_spot_order_events(
        &self,
        config: &BinanceSpotChannelConfig,
    ) -> Result<blocking::BinanceSpotOrderEvents, IntegrationError> {
        let (endpoint, capacity) = Self::spot_channel(config)?;
        BinanceSpotOrderEventSource::from_client_with_capacity(
            self.binding_id.clone(),
            self.client.clone(),
            endpoint,
            capacity,
        )
        .map(|inner| blocking::BinanceSpotOrderEvents { inner })
    }

    #[cfg(test)]
    fn shares_provider_http_with(&self, other: &Self) -> bool {
        self.client.shares_http_worker_with(&other.client)
    }
}

impl BinanceFuturesPrincipalConnection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn order_entry(&self) -> BinanceFuturesOrderEntry {
        BinanceFuturesOrderEntry {
            inner: BinanceFuturesOrderConnection::from_client(self.client.clone()),
        }
    }

    pub fn order_query(&self) -> BinanceFuturesOrderQuery {
        BinanceFuturesOrderQuery {
            inner: BinanceOrderQueryConnection::futures_from_client(self.client.clone()),
        }
    }

    pub fn order_events(
        &self,
        config: &BinanceFuturesChannelConfig,
    ) -> Result<BinanceFuturesOrderEvents, IntegrationError> {
        BinanceFuturesAsyncOrderEventSource::new(
            self.descriptor.binding_id.clone(),
            self.client.clone(),
            config.websocket_stream_url.clone(),
            config.event_queue_capacity,
        )
        .map(|inner| BinanceFuturesOrderEvents { inner })
    }
}

impl BinanceMarginPrincipalConnection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn order_entry(&self) -> Result<BinanceMarginOrderEntry, IntegrationError> {
        BinanceMarginOrderConnection::from_client(
            self.client.clone(),
            self.domain,
            self.isolated_symbol.clone(),
        )
        .map(|inner| BinanceMarginOrderEntry { inner })
    }

    pub fn order_query(&self) -> Result<BinanceMarginOrderQuery, IntegrationError> {
        BinanceMarginOrderQueryConnection::new(
            self.client.clone(),
            self.domain,
            self.isolated_symbol.clone(),
        )
        .map(|inner| BinanceMarginOrderQuery { inner })
    }

    pub fn order_events(
        &self,
        config: &BinanceMarginChannelConfig,
    ) -> Result<BinanceMarginOrderEvents, IntegrationError> {
        if self.domain == ConnectionDomain::CrossMargin && config.isolated_symbol.is_some() {
            return Err(IntegrationError::InvalidRequest(
                "cross-margin stream must not configure an isolated symbol".into(),
            ));
        }
        let isolated_symbol = match (&self.isolated_symbol, &config.isolated_symbol) {
            (Some(expected), Some(configured)) if expected != &configured.to_ascii_uppercase() => {
                return Err(IntegrationError::InvalidRequest(format!(
                    "isolated-margin stream symbol {} does not match route symbol {expected}",
                    configured
                )));
            }
            (Some(expected), _) => Some(expected.clone()),
            (None, configured) => configured.clone(),
        };
        BinanceAsyncMarginOrderEventSource::new(
            self.descriptor.binding_id.clone(),
            self.client.clone(),
            config.websocket_stream_url.clone(),
            isolated_symbol,
            config.event_queue_capacity,
        )
        .map(|inner| BinanceMarginOrderEvents { inner })
    }
}

impl BinanceOptionsPrincipalConnection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn order_entry(&self) -> BinanceOptionsOrderEntry {
        BinanceOptionsOrderEntry {
            inner: BinanceOptionsOrderConnection::from_client(self.client.clone()),
        }
    }

    pub fn order_query(&self) -> BinanceOptionsOrderQuery {
        BinanceOptionsOrderQuery {
            inner: BinanceOrderQueryConnection::options_from_client(self.client.clone()),
        }
    }

    pub fn order_events(
        &self,
        config: &BinanceOptionsChannelConfig,
    ) -> Result<BinanceOptionsOrderEvents, IntegrationError> {
        BinanceOptionsAsyncOrderEventSource::new(
            self.descriptor.binding_id.clone(),
            self.client.clone(),
            config.websocket_stream_url.clone(),
            config.event_queue_capacity,
        )
        .map(|inner| BinanceOptionsOrderEvents { inner })
    }
}

#[cfg(test)]
mod tests;
