//! Binance participant-native connection facade.

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
use crate::services::participants::binance::equity::{
    client::BinanceEquityRestClient, normalizers as equity_instruments,
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
    BinanceSpotProviderRuntime, PrincipalOrderQuota, QuotaAllocation, RequestPriority,
};
use crate::services::participants::binance::{
    funding::{account as funding, earn, transfer},
    market_data::catalog as instrument_catalog,
};
use crate::services::quota::SharedFixedWindowQuota;
use crate::services::transport::http::PublicHttpClient;

use super::config::{BinanceConnectionConfig, BinancePrincipalConfig, BinanceSpotChannelConfig};
use super::types::{ConnectionDomain, InstrumentType};

/// Provider/IP scope shared by all Binance Spot principals in one business
/// process. It owns the endpoint set and one HTTP scheduling lane.
pub struct BinanceConnection {
    config: BinanceConnectionConfig,
    runtime: BinanceSpotProviderRuntime,
}

pub struct BinanceInstrumentCatalog {
    descriptor: ConnectionDescriptor,
    instrument_type: InstrumentType,
    base_url: String,
    runtime: BinanceSpotProviderRuntime,
}

/// One provider/principal context. Capability handles projected from this
/// object share the same authenticated HTTP client and worker.
pub struct BinancePrincipalConnection {
    binding_id: String,
    participant: ParticipantRef,
    environment: String,
    principal_id: Option<String>,
    client: BinanceSpotAccountClient,
    equity_client: BinanceEquityRestClient,
}

pub struct BinanceEquityInstrumentCatalog {
    descriptor: ConnectionDescriptor,
    client: BinanceEquityRestClient,
}

pub struct BinanceSpotOrderEntry {
    inner: BinanceSpotOrderConnection,
}

pub struct BinanceSpotOrderQuery {
    inner: BinanceOrderQueryConnection,
}

pub struct BinanceSpotOrderEvents {
    inner: BinanceSpotAsyncOrderEventSource,
}

pub struct BinanceSpotAccountEvents {
    inner: BinanceSpotAsyncAccountEventSource,
}

pub struct BinanceSpotAccountRead {
    client: BinanceSpotAccountClient,
}

pub struct BinanceSpotAccountMarketProfile {
    client: BinanceSpotAccountClient,
}

/// Funding-wallet projection from the same Binance provider/principal
/// context used by Spot. It shares credentials, clock, HTTP scheduling and
/// host-local quota state with the other projections.
pub struct BinanceFundingAccountRead {
    descriptor: ConnectionDescriptor,
    client: BinanceSpotAccountClient,
}

pub struct BinanceFundingCredentialInspection {
    descriptor: ConnectionDescriptor,
    client: BinanceSpotAccountClient,
}

/// Simple Earn is an operation capability within the Funding connection
/// domain, not a separate financial product family.
pub struct BinanceSimpleEarn {
    descriptor: ConnectionDescriptor,
    client: BinanceSpotAccountClient,
}

/// Internal wallet transfer uses the same authenticated principal context.
pub struct BinanceTransfer {
    descriptor: ConnectionDescriptor,
    client: BinanceSpotAccountClient,
}

/// Explicit blocking compatibility facade. New async business runtimes use
/// [`super::BinanceSpotOrderEvents`] instead.
pub mod blocking {
    use std::collections::BTreeMap;

    use crate::application::capabilities::account_facts::{
        ExternalAccountSegment, ExternalAccountSnapshot,
    };
    use crate::application::capabilities::reference::{
        ExternalInstrumentCatalog, InstrumentCatalogConnection,
    };
    use crate::application::capabilities::{ConnectionHealth, OrderEntryEvent, OrderEntryRequest};
    use crate::application::{
        CommandResult, EarnActionResult, EarnPosition, EarnProduct, EarnProductType,
        EarnRedeemRequest, EarnReward, EarnSubscribeRequest, ExternalAccountCredentialProfile,
        ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
        IntegrationError, OrderEntryConnection, OrderEventSource, OrderQueryConnection,
        TransferRequest, TransferResult,
    };
    use crate::services::participants::binance::equity::{
        client::BinanceEquityRestClient, normalizers as equity_instruments,
    };
    use crate::services::participants::binance::funding::{account as funding, earn, transfer};
    use crate::services::participants::binance::order_query::BinanceOrderQueryConnection;
    use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;
    use crate::services::participants::binance::spot::order::BinanceSpotOrderConnection;
    use crate::services::participants::binance::spot::order_events::BinanceSpotOrderEventSource;
    use crate::services::transport::http::command_error_outcome;

    use super::{
        instrument_catalog, map_exchange_error, BinanceSpotProviderRuntime, ConnectionDescriptor,
        InstrumentType, RequestPriority,
    };

    pub struct BinanceSpotOrderEntry {
        pub(super) inner: BinanceSpotOrderConnection,
    }

    pub struct BinanceSpotOrderQuery {
        pub(super) inner: BinanceOrderQueryConnection,
    }

    pub struct BinanceSpotOrderEvents {
        pub(super) inner: BinanceSpotOrderEventSource,
    }

    pub struct BinanceFundingAccountRead {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: BinanceSpotAccountClient,
    }

    pub struct BinanceFundingCredentialInspection {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: BinanceSpotAccountClient,
    }

    pub struct BinanceSimpleEarn {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: BinanceSpotAccountClient,
    }

    pub struct BinanceTransfer {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: BinanceSpotAccountClient,
    }

    pub struct BinanceInstrumentCatalog {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) instrument_type: InstrumentType,
        pub(super) base_url: String,
        pub(super) runtime: BinanceSpotProviderRuntime,
    }

    pub struct BinanceEquityInstrumentCatalog {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: BinanceEquityRestClient,
    }

    fn reject_async_runtime() -> Result<(), IntegrationError> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(IntegrationError::InvalidRequest(
                "kairos_integration::blocking cannot run on a Tokio runtime worker; use the default async capability or move the blocking call to a dedicated thread".into(),
            ));
        }
        Ok(())
    }

    impl OrderEntryConnection for BinanceSpotOrderEntry {
        fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent> {
            reject_async_runtime()?;
            self.inner.submit_order(request)
        }

        fn cancel_order(
            &mut self,
            request: &OrderEntryRequest,
            remote_order_id: &str,
            at_unix_nanos: u64,
        ) -> CommandResult<OrderEntryEvent> {
            reject_async_runtime()?;
            self.inner
                .cancel_order(request, remote_order_id, at_unix_nanos)
        }
    }

    impl OrderQueryConnection for BinanceSpotOrderQuery {
        fn open_orders(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            self.inner.open_orders(query)
        }

        fn order_history(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            self.inner.order_history(query)
        }

        fn order_detail(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            self.inner.order_detail(query)
        }
    }

    impl OrderEventSource for BinanceSpotOrderEvents {
        fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            self.inner.connect_channel()
        }

        fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            self.inner.disconnect_channel()
        }

        fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            self.inner.reconnect_channel()
        }

        fn channel_health(&self) -> ConnectionHealth {
            self.inner.channel_health()
        }

        fn try_next_order_event(
            &mut self,
        ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>
        {
            reject_async_runtime()?;
            self.inner.try_next_order_event()
        }
    }

    impl BinanceFundingAccountRead {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn fetch_account(
            &mut self,
            segment: &ExternalAccountSegment,
        ) -> Result<ExternalAccountSnapshot, IntegrationError> {
            reject_async_runtime()?;
            let payload = self
                .client
                .signed_post_query("/sapi/v1/asset/get-funding-asset", BTreeMap::new())
                .map_err(super::map_exchange_error)?;
            funding::normalize_funding(segment, &payload).map_err(IntegrationError::InvalidPayload)
        }
    }

    impl BinanceFundingCredentialInspection {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn inspect_credential(
            &mut self,
        ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
            reject_async_runtime()?;
            let payload = self.client.account().map_err(super::map_exchange_error)?;
            Ok(funding::normalize_credential_profile(&payload))
        }
    }

    impl BinanceSimpleEarn {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn products(
            &mut self,
            asset: Option<&str>,
            product_type: Option<EarnProductType>,
        ) -> Result<Vec<EarnProduct>, IntegrationError> {
            reject_async_runtime()?;
            let mut params = BTreeMap::new();
            if let Some(asset) = asset {
                params.insert("asset".into(), asset.into());
            }
            if let Some(product_type) = product_type {
                params.insert(
                    "productType".into(),
                    earn::product_type_name(product_type).to_ascii_uppercase(),
                );
            }
            let payload = self
                .client
                .signed_get("/sapi/v1/simple-earn/products", params)
                .map_err(super::map_exchange_error)?;
            Ok(payload
                .get("rows")
                .and_then(serde_json::Value::as_array)
                .map(|rows| rows.iter().filter_map(earn::normalize_product).collect())
                .unwrap_or_default())
        }

        pub fn positions(
            &mut self,
            asset: Option<&str>,
        ) -> Result<Vec<EarnPosition>, IntegrationError> {
            reject_async_runtime()?;
            let params = asset
                .map(|asset| BTreeMap::from([("asset".into(), asset.into())]))
                .unwrap_or_default();
            let payload = self
                .client
                .signed_get("/sapi/v1/simple-earn/positions", params)
                .map_err(super::map_exchange_error)?;
            Ok(payload
                .get("rows")
                .and_then(serde_json::Value::as_array)
                .map(|rows| rows.iter().filter_map(earn::normalize_position).collect())
                .unwrap_or_default())
        }

        pub fn rewards(
            &mut self,
            asset: Option<&str>,
        ) -> Result<Vec<EarnReward>, IntegrationError> {
            reject_async_runtime()?;
            let params = asset
                .map(|asset| BTreeMap::from([("asset".into(), asset.into())]))
                .unwrap_or_default();
            let payload = self
                .client
                .signed_get("/sapi/v1/simple-earn/rewardsRecord", params)
                .map_err(super::map_exchange_error)?;
            Ok(payload
                .get("rows")
                .and_then(serde_json::Value::as_array)
                .map(|rows| rows.iter().filter_map(earn::normalize_reward).collect())
                .unwrap_or_default())
        }

        pub fn subscribe(
            &mut self,
            request: &EarnSubscribeRequest,
        ) -> CommandResult<EarnActionResult> {
            reject_async_runtime()?;
            let path = match request.product_type {
                EarnProductType::Locked => "/sapi/v1/simple-earn/locked/subscribe",
                EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/subscribe",
            };
            let mut params = BTreeMap::from([
                (String::from("productId"), request.product_id.clone()),
                (String::from("amount"), request.amount.to_string()),
            ]);
            if let Some(auto_renew) = request.auto_renew {
                params.insert("autoSubscribe".into(), auto_renew.to_string());
            }
            let payload = match self.client.signed_post(path, params) {
                Ok(payload) => payload,
                Err(error) => return command_error_outcome(error),
            };
            Ok(earn::normalize_action(&payload))
        }

        pub fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnActionResult> {
            reject_async_runtime()?;
            let path = match request.product_type {
                EarnProductType::Locked => "/sapi/v1/simple-earn/locked/redeem",
                EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/redeem",
            };
            let mut params =
                BTreeMap::from([(String::from("productId"), request.product_id.clone())]);
            if let Some(amount) = &request.amount {
                params.insert("amount".into(), amount.to_string());
            }
            if let Some(destination) = &request.destination_account {
                params.insert("destAccount".into(), destination.clone());
            }
            let payload = match self.client.signed_post(path, params) {
                Ok(payload) => payload,
                Err(error) => return command_error_outcome(error),
            };
            Ok(earn::normalize_action(&payload))
        }
    }

    impl BinanceTransfer {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn transfer(&mut self, request: &TransferRequest) -> CommandResult<TransferResult> {
            reject_async_runtime()?;
            let params = transfer::transfer_params(request)?;
            let payload = match self.client.signed_post("/sapi/v1/asset/transfer", params) {
                Ok(payload) => payload,
                Err(error) => return command_error_outcome(error),
            };
            Ok(transfer::normalize_transfer(&payload))
        }
    }

    impl BinanceInstrumentCatalog {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl InstrumentCatalogConnection for BinanceInstrumentCatalog {
        fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
            reject_async_runtime()?;
            self.runtime
                .acquire(1, RequestPriority::Background)
                .map_err(map_exchange_error)?;
            let response = self
                .runtime
                .http()
                .get_json_response_with_headers_and_query(
                    &format!("{}{}", self.base_url, self.instrument_type.path()),
                    &[],
                    &[],
                )
                .map_err(map_exchange_error)?;
            self.runtime.observe_response(&response);
            match self.instrument_type {
                InstrumentType::Spot => instrument_catalog::normalize_spot(&response.body),
                InstrumentType::UsdMFutures | InstrumentType::CoinMFutures => {
                    instrument_catalog::normalize_derivatives(&response.body)
                }
                InstrumentType::Option => instrument_catalog::normalize_options(&response.body),
            }
        }
    }

    impl BinanceEquityInstrumentCatalog {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl InstrumentCatalogConnection for BinanceEquityInstrumentCatalog {
        fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
            reject_async_runtime()?;
            let payload = self.client.exchange_info().map_err(map_exchange_error)?;
            equity_instruments::catalog(&payload)
        }
    }

    #[cfg(test)]
    mod tests {
        #[tokio::test]
        async fn blocking_facade_rejects_tokio_runtime_workers() {
            let error = super::reject_async_runtime().unwrap_err();
            assert!(matches!(
                error,
                crate::application::IntegrationError::InvalidRequest(_)
            ));
        }
    }
}

impl BinanceConnection {
    pub fn connect(mut config: BinanceConnectionConfig) -> Result<Self, IntegrationError> {
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
                        "binance:{}:egress:{}:request-weight-1m",
                        config.environment,
                        shared.egress_scope_id.trim()
                    ),
                    config.quota.request_weight_per_minute,
                    config.quota.cancel_reserve_weight,
                    60_000,
                )
                .map_err(|error| {
                    IntegrationError::Unavailable(format!(
                        "open Binance shared quota ledger: {error}"
                    ))
                })
            })
            .transpose()?;
        let runtime = BinanceSpotProviderRuntime::new_with_shared_quota(
            http,
            QuotaAllocation {
                request_weight_per_minute: config.quota.request_weight_per_minute,
                cancel_reserve_weight: config.quota.cancel_reserve_weight,
            },
            shared_quota,
        )
        .map_err(map_exchange_error)?;
        Ok(Self { config, runtime })
    }

    pub fn instrument_catalog(&self, instrument_type: InstrumentType) -> BinanceInstrumentCatalog {
        BinanceInstrumentCatalog {
            descriptor: self.public_descriptor(instrument_type),
            instrument_type,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }

    pub fn blocking_instrument_catalog(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::BinanceInstrumentCatalog {
        blocking::BinanceInstrumentCatalog {
            descriptor: self.public_descriptor(instrument_type),
            instrument_type,
            base_url: self.config.rest_base_url.clone(),
            runtime: self.runtime.clone(),
        }
    }

    fn public_descriptor(&self, instrument_type: InstrumentType) -> ConnectionDescriptor {
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
            environment: self.config.environment.clone(),
            principal_id: None,
            domain: domain.into(),
        }
    }

    pub fn principal_connection(
        &self,
        config: BinancePrincipalConfig,
    ) -> Result<BinancePrincipalConnection, IntegrationError> {
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
                let shared = self.config.shared_quota.as_ref().ok_or_else(|| {
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
                            "binance:{}:principal:{}:{rate_limit_id}",
                            self.config.environment, principal_id
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
                self.runtime.with_principal_order_quotas(vec![
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
            None => self.runtime.clone(),
        };
        let api_key = config.api_key.expose_secret().to_owned();
        let secret = config.secret.expose_secret().to_owned();
        let client = BinanceSpotAccountClient::from_runtime(
            runtime,
            api_key.clone(),
            secret.clone(),
            self.config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        let equity_client = BinanceEquityRestClient::with_base_url(
            api_key,
            secret,
            self.config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        Ok(BinancePrincipalConnection {
            binding_id,
            participant: ParticipantRef::new(ParticipantKind::Exchange, "binance")
                .expect("static Binance participant"),
            environment: self.config.environment.clone(),
            principal_id: config.principal_id,
            client,
            equity_client,
        })
    }
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

impl BinanceInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceEquityInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for BinanceInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        self.runtime
            .acquire(1, RequestPriority::Background)
            .map_err(map_exchange_error)?;
        let response = self
            .runtime
            .async_http()
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.base_url, self.instrument_type.path()),
                &[],
                &[],
            )
            .await
            .map_err(map_exchange_error)?;
        self.runtime.observe_response(&response);
        match self.instrument_type {
            InstrumentType::Spot => instrument_catalog::normalize_spot(&response.body),
            InstrumentType::UsdMFutures | InstrumentType::CoinMFutures => {
                instrument_catalog::normalize_derivatives(&response.body)
            }
            InstrumentType::Option => instrument_catalog::normalize_options(&response.body),
        }
    }
}

impl AsyncInstrumentCatalogConnection for BinanceEquityInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let payload = self
            .client
            .exchange_info_async()
            .await
            .map_err(map_exchange_error)?;
        equity_instruments::catalog(&payload)
    }
}

impl BinancePrincipalConnection {
    pub fn spot_descriptor(&self) -> ConnectionDescriptor {
        self.domain_descriptor(ConnectionDomain::Spot)
    }

    pub fn equity_instrument_catalog(&self) -> BinanceEquityInstrumentCatalog {
        BinanceEquityInstrumentCatalog {
            descriptor: self.domain_descriptor(ConnectionDomain::Equity),
            client: self.equity_client.clone(),
        }
    }

    pub fn blocking_equity_instrument_catalog(&self) -> blocking::BinanceEquityInstrumentCatalog {
        blocking::BinanceEquityInstrumentCatalog {
            descriptor: self.domain_descriptor(ConnectionDomain::Equity),
            client: self.equity_client.clone(),
        }
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

impl BinanceFundingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceFundingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceSimpleEarn {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceTransfer {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncOrderEntryConnection for BinanceSpotOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit_order_async(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel_order_async(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderQueryConnection for BinanceSpotOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders_async(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history_async(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail_async(query).await
    }
}

impl AsyncOrderEventSource for BinanceSpotOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<
        crate::application::ExternalEventEnvelope<crate::application::ExternalExecutionEvent>,
        IntegrationError,
    > {
        self.inner.next_order_event().await
    }
}

impl AsyncAccountEventSource for BinanceSpotAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEvent,
        IntegrationError,
    > {
        self.inner.next_account_event().await
    }
}

impl AsyncAccountReadConnection for BinanceSpotAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let account = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let orders = self
            .client
            .open_orders_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_account(segment, &account, &orders).map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountReadConnection for BinanceFundingAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let payload = self
            .client
            .signed_post_query_async("/sapi/v1/asset/get-funding-asset", Default::default())
            .await
            .map_err(map_exchange_error)?;
        funding::normalize_funding(segment, &payload).map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceFundingCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        Ok(funding::normalize_credential_profile(&payload))
    }
}

impl AsyncEarnConnection for BinanceSimpleEarn {
    async fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, IntegrationError> {
        let mut params = std::collections::BTreeMap::new();
        if let Some(asset) = asset {
            params.insert("asset".into(), asset.into());
        }
        if let Some(product_type) = product_type {
            params.insert(
                "productType".into(),
                earn::product_type_name(product_type).to_ascii_uppercase(),
            );
        }
        let payload = self
            .client
            .signed_get_async("/sapi/v1/simple-earn/products", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_product).collect())
            .unwrap_or_default())
    }

    async fn positions(
        &mut self,
        asset: Option<&str>,
    ) -> Result<Vec<EarnPosition>, IntegrationError> {
        let params = asset
            .map(|asset| std::collections::BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get_async("/sapi/v1/simple-earn/positions", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_position).collect())
            .unwrap_or_default())
    }

    async fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, IntegrationError> {
        let params = asset
            .map(|asset| std::collections::BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get_async("/sapi/v1/simple-earn/rewardsRecord", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_reward).collect())
            .unwrap_or_default())
    }

    async fn subscribe(
        &mut self,
        request: &EarnSubscribeRequest,
    ) -> CommandResult<EarnActionResult> {
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/subscribe",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/subscribe",
        };
        let mut params = std::collections::BTreeMap::from([
            (String::from("productId"), request.product_id.clone()),
            (String::from("amount"), request.amount.to_string()),
        ]);
        if let Some(auto_renew) = request.auto_renew {
            params.insert("autoSubscribe".into(), auto_renew.to_string());
        }
        let payload = match self.client.signed_post_async(path, params).await {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }

    async fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnActionResult> {
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/redeem",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/redeem",
        };
        let mut params = std::collections::BTreeMap::from([(
            String::from("productId"),
            request.product_id.clone(),
        )]);
        if let Some(amount) = &request.amount {
            params.insert("amount".into(), amount.to_string());
        }
        if let Some(destination) = &request.destination_account {
            params.insert("destAccount".into(), destination.clone());
        }
        let payload = match self.client.signed_post_async(path, params).await {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }
}

impl AsyncTransferConnection for BinanceTransfer {
    async fn transfer(&mut self, request: &TransferRequest) -> CommandResult<TransferResult> {
        let params = transfer::transfer_params(request)?;
        let payload = match self
            .client
            .signed_post_async("/sapi/v1/asset/transfer", params)
            .await
        {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(transfer::normalize_transfer(&payload))
    }
}

impl AsyncAccountMarketProfileConnection for BinanceSpotAccountMarketProfile {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance market profile symbol is required".into(),
            ));
        }
        let fee = self
            .client
            .trade_fee_async(request.source_symbol.as_str())
            .await
            .map_err(map_exchange_error)?;
        let account = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let burn = self
            .client
            .bnb_burn_status_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_market_profile(request, &fee, &account, &burn)
            .map_err(IntegrationError::InvalidPayload)
    }
}

#[cfg(test)]
mod tests {
    use super::{
        BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, ConnectionDomain,
        InstrumentType,
    };
    use crate::application::participants::binance::BinanceQuotaAllocation;
    use secrecy::SecretString;

    use crate::application::capabilities::reference::{
        AsyncInstrumentCatalogConnection, InstrumentCatalogConnection,
    };
    use crate::application::capabilities::{
        DecimalValue, OrderEntryRequest, OrderSide, OrderType, ParticipantKind, ParticipantRef,
        ProviderInstrumentRef,
    };
    use crate::application::{
        AsyncAccountCredentialInspectionConnection, AsyncAccountReadConnection,
        AsyncEarnConnection, AsyncOrderEntryConnection, AsyncOrderQueryConnection,
        AsyncTransferConnection,
    };

    #[test]
    fn native_connection_validates_binding_and_projects_shared_capabilities() {
        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "testnet".into(),
            rest_base_url: "https://testnet.binance.vision".into(),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let connection = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "execution.binance.primary".into(),
                principal_id: Some("account-a".into()),
                api_key: SecretString::from("key"),
                secret: SecretString::from("secret"),
                principal_quota: None,
            })
            .unwrap();

        assert_eq!(connection.spot_descriptor().participant.id, "binance");
        assert_eq!(connection.spot_descriptor().domain.as_str(), "spot");
        assert_eq!(
            connection.spot_descriptor().principal_id.as_deref(),
            Some("account-a")
        );
        connection.spot_order_entry().unwrap();
        connection.spot_order_query().unwrap();
        connection
            .spot_order_events(&super::BinanceSpotChannelConfig {
                websocket_api_url: "wss://ws-api.testnet.binance.vision/ws-api/v3".into(),
                event_queue_capacity: 1_024,
            })
            .unwrap();
    }

    #[test]
    fn funding_handles_share_one_connection_domain_without_capability_metadata() {
        fn is_account_read<T: AsyncAccountReadConnection>(_: &T) {}
        fn is_credential_inspection<T: AsyncAccountCredentialInspectionConnection>(_: &T) {}
        fn is_earn<T: AsyncEarnConnection>(_: &T) {}
        fn is_transfer<T: AsyncTransferConnection>(_: &T) {}

        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "testnet".into(),
            rest_base_url: "https://testnet.binance.vision".into(),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let connection = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "account.binance.main".into(),
                principal_id: Some("main".into()),
                api_key: SecretString::from("key"),
                secret: SecretString::from("secret"),
                principal_quota: None,
            })
            .unwrap();

        let account_read = connection.funding_account_read();
        let inspection = connection.funding_credential_inspection();
        let earn = connection.earn();
        let transfer = connection.transfer();
        let blocking_account_read = connection.blocking_funding_account_read();
        let blocking_inspection = connection.blocking_funding_credential_inspection();
        let blocking_earn = connection.blocking_earn();
        let blocking_transfer = connection.blocking_transfer();
        is_account_read(&account_read);
        is_credential_inspection(&inspection);
        is_earn(&earn);
        is_transfer(&transfer);
        for descriptor in [
            account_read.descriptor(),
            inspection.descriptor(),
            earn.descriptor(),
            transfer.descriptor(),
        ] {
            assert_eq!(
                descriptor.domain.as_str(),
                ConnectionDomain::Funding.as_str()
            );
            assert_eq!(descriptor.participant.id, "binance");
            assert_eq!(descriptor.principal_id.as_deref(), Some("main"));
        }
        assert_eq!(account_read.descriptor(), earn.descriptor());
        assert_eq!(account_read.descriptor(), inspection.descriptor());
        assert_eq!(earn.descriptor(), transfer.descriptor());
        assert_eq!(
            account_read.descriptor(),
            blocking_account_read.descriptor()
        );
        assert_eq!(earn.descriptor(), blocking_earn.descriptor());
        assert_eq!(inspection.descriptor(), blocking_inspection.descriptor());
        assert_eq!(transfer.descriptor(), blocking_transfer.descriptor());
    }

    #[test]
    fn provider_context_shares_ip_http_lane_but_separates_principals() {
        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "live".into(),
            rest_base_url: "https://api.binance.com".into(),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let main = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "execution.binance.main".into(),
                principal_id: Some("main".into()),
                api_key: SecretString::from("main-key"),
                secret: SecretString::from("main-secret"),
                principal_quota: None,
            })
            .unwrap();
        let hedge = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "execution.binance.hedge".into(),
                principal_id: Some("hedge".into()),
                api_key: SecretString::from("hedge-key"),
                secret: SecretString::from("hedge-secret"),
                principal_quota: None,
            })
            .unwrap();

        assert!(main.shares_provider_http_with(&hedge));
        assert_ne!(
            main.spot_descriptor().binding_id,
            hedge.spot_descriptor().binding_id
        );
        assert_ne!(
            main.spot_descriptor().principal_id,
            hedge.spot_descriptor().principal_id
        );
    }

    #[test]
    fn spot_channel_validates_transport_without_network_access() {
        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "live".into(),
            rest_base_url: "https://api.binance.com".into(),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let connection = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "execution.binance.validation".into(),
                principal_id: Some("validation".into()),
                api_key: SecretString::from("key"),
                secret: SecretString::from("secret"),
                principal_quota: None,
            })
            .unwrap();
        let error = connection
            .spot_order_events(&super::BinanceSpotChannelConfig {
                websocket_api_url: "https://ws-api.binance.com".into(),
                event_queue_capacity: 8,
            })
            .err()
            .unwrap();
        assert!(matches!(
            error,
            crate::application::IntegrationError::InvalidRequest(_)
        ));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn instrument_catalog_uses_the_callers_runtime_and_trait_is_the_capability() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        fn is_async<T: AsyncInstrumentCatalogConnection>(_: &T) {}
        fn is_blocking<T: InstrumentCatalogConnection>(_: &T) {}

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 4096];
            let read = stream.read(&mut request).await.unwrap();
            assert!(
                String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/exchangeInfo ")
            );
            let body = r#"{"symbols":[{"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","baseAssetPrecision":6,"quoteAssetPrecision":2}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).await.unwrap();
        });
        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "public".into(),
            rest_base_url: format!("http://{address}"),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let mut catalog = provider.instrument_catalog(InstrumentType::Spot);
        let blocking = provider.blocking_instrument_catalog(InstrumentType::Spot);
        is_async(&catalog);
        is_blocking(&blocking);
        assert_eq!(catalog.descriptor().domain.as_str(), "spot");
        assert!(catalog.descriptor().principal_id.is_none());

        let facts = catalog.fetch_instruments().await.unwrap();
        assert_eq!(facts.participant.id.as_str(), "binance");
        assert_eq!(facts.instruments[0].source_symbol, "BTCUSDT");
        server.await.unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_order_entry_and_query_use_the_callers_runtime() {
        use std::io::{Read, Write};

        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            for index in 0..3 {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0_u8; 8_192];
                let read = stream.read(&mut request).unwrap();
                let request = String::from_utf8_lossy(&request[..read]);
                let body = match index {
                    0 => {
                        assert!(request.starts_with("GET /api/v3/time "));
                        serde_json::json!({
                            "serverTime": std::time::SystemTime::now()
                                .duration_since(std::time::UNIX_EPOCH)
                                .unwrap()
                                .as_millis() as u64
                        })
                        .to_string()
                    }
                    1 => {
                        assert!(request.starts_with("POST /api/v3/order?"));
                        r#"{"orderId":123,"status":"NEW","executedQty":"0.00"}"#.into()
                    }
                    _ => {
                        assert!(request.starts_with("GET /api/v3/openOrders?"));
                        r#"[{"orderId":"123","clientOrderId":"order-async-1","symbol":"BTCUSDT","side":"BUY","type":"MARKET","status":"NEW","origQty":"0.25","executedQty":"0","price":"0","time":1000}]"#.into()
                    }
                };
                write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nX-MBX-USED-WEIGHT-1M: {}\r\n\r\n{}",
                    body.len(),
                    index + 1,
                    body
                )
                .unwrap();
            }
        });

        let provider = BinanceConnection::connect(BinanceConnectionConfig {
            environment: "test".into(),
            rest_base_url: format!("http://{address}"),
            quota: BinanceQuotaAllocation {
                request_weight_per_minute: 1_000,
                cancel_reserve_weight: 50,
            },
            shared_quota: None,
        })
        .unwrap();
        let connection = provider
            .principal_connection(BinancePrincipalConfig {
                binding_id: "execution.binance.async-http".into(),
                principal_id: Some("async-http".into()),
                api_key: SecretString::from("api-key"),
                secret: SecretString::from("secret"),
                principal_quota: None,
            })
            .unwrap();
        let request = OrderEntryRequest {
            order_id: kairos_domain_types::OrderId::new("order-async-1").unwrap(),
            intent_id: None,
            account_id: kairos_domain_types::AccountId::new("main").unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-usdt").unwrap(),
            market_id: None,
            provider_instrument: ProviderInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
                Some(ConnectionDomain::Spot.into()),
                "BTCUSDT",
            )
            .unwrap(),
            side: OrderSide::Buy,
            quantity: DecimalValue::new(25, 2),
            order_type: OrderType::Market,
            limit_price: None,
            options: Default::default(),
        };
        let mut entry = connection.spot_order_entry().unwrap();
        let outcome = AsyncOrderEntryConnection::submit_order(&mut entry, &request)
            .await
            .unwrap();
        assert!(matches!(
            outcome,
            crate::application::CommandOutcome::Confirmed(_)
        ));

        let mut query = connection.spot_order_query().unwrap();
        let rows = AsyncOrderQueryConnection::open_orders(
            &mut query,
            &crate::application::ExternalOrderQuery {
                symbol: Some(kairos_domain_types::Symbol::new("BTCUSDT").unwrap()),
                ..Default::default()
            },
        )
        .await
        .unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].order_id, "123");
        server.join().unwrap();
    }
}
