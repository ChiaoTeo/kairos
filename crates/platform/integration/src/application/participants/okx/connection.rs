//! OKX participant-native connection facade and capability projections.

mod account;
pub mod blocking;
mod execution;
mod market_data;
mod reference;

pub use account::{
    OkxTradingAccountEvents, OkxTradingAccountMarketProfile, OkxTradingAccountRead,
    OkxTradingCredentialInspection,
};
pub use execution::{OkxTradingOrderEntry, OkxTradingOrderEvents, OkxTradingOrderQuery};
pub use market_data::OkxLiveMarket;
pub use reference::{OkxInstrumentCatalog, OkxMarketSnapshot};

use std::collections::VecDeque;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::Utc;
use kairos_primitives::{Currency, Price, ProviderSymbol, Quantity, Symbol, UnixNanos};
use secrecy::{ExposeSecret, SecretString};
use tokio_tungstenite::tungstenite::Message;

use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrument, ExternalInstrumentCatalog,
    ExternalInstrumentKind, InstrumentCatalogConnection,
};
use crate::application::capabilities::{
    ConnectionHealth, ConnectionLifecycle, OrderEntryEvent, OrderEntryRequest, ParticipantKind,
    ParticipantRef,
};
use crate::application::{
    AsyncAccountCredentialInspectionConnection, AsyncAccountEventSource,
    AsyncAccountMarketProfileConnection, AsyncAccountReadConnection, AsyncMarketSnapshotConnection,
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandResult,
    ConnectionDescriptor, ExternalAccountCredentialProfile, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalMarketProfile, ExternalMarketProfileRequest, ExternalOrder,
    ExternalOrderQuery, IntegrationError, MarketEvent, MarketEventKind, OrderEntryConnection,
    OrderQueryConnection,
};
use crate::services::participants::okx::signing::okx_signature;
use crate::services::participants::okx::stream::{
    login_succeeded, parse_event, parse_execution_events,
};
use crate::services::participants::okx::{
    cancel_request_body, normalize_account, normalize_credential_profile, normalize_market_profile,
    normalize_okx_order, normalize_okx_orders, normalize_order_cancellation,
    normalize_order_submission, order_request_body, OkxAccountClient,
};
use crate::services::quota::{SharedFixedWindowQuota, SharedQuotaExhausted, SharedQuotaPriority};
use crate::services::transport::http::{
    command_error_outcome, AsyncPublicHttpClient, ExchangeError, PublicHttpClient,
};
use crate::services::transport::websocket::{AsyncSocketEvent, AsyncTokioSocket};

use super::config::{OkxConnectionConfig, OkxPrincipalConfig, OkxPrivateChannelConfig};
use super::types::{ConnectionDomain, InstrumentType, TradingMode};
use reference::normalize_instrument_catalog;

/// Provider/egress context. HTTP clients are created once here and shared by
/// every principal and capability projection in the owning business process.
pub struct OkxConnection {
    config: OkxConnectionConfig,
    blocking_http: PublicHttpClient,
    async_http: AsyncPublicHttpClient,
}

pub struct OkxPrincipalConnection {
    binding_id: String,
    participant: ParticipantRef,
    environment: String,
    principal_id: Option<String>,
    client: OkxAccountClient,
    private_query_quota: Option<Arc<SharedFixedWindowQuota>>,
    private_order_quota: Option<Arc<SharedFixedWindowQuota>>,
    api_key: SecretString,
    secret: SecretString,
    passphrase: SecretString,
}

impl OkxConnection {
    pub fn connect(mut config: OkxConnectionConfig) -> Result<Self, IntegrationError> {
        config.environment = config.environment.trim().to_owned();
        config.rest_base_url = config.rest_base_url.trim_end_matches('/').to_owned();
        if config.environment.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX environment is required".into(),
            ));
        }
        if !(config.rest_base_url.starts_with("https://")
            || config.rest_base_url.starts_with("http://"))
        {
            return Err(IntegrationError::InvalidRequest(
                "OKX REST endpoint must start with http:// or https://".into(),
            ));
        }
        if config
            .shared_quota
            .as_ref()
            .is_some_and(|quota| quota.egress_scope_id.trim().is_empty())
        {
            return Err(IntegrationError::InvalidRequest(
                "OKX egress scope id is required".into(),
            ));
        }
        Ok(Self {
            config,
            blocking_http: PublicHttpClient::new("kairos-integration/okx")
                .map_err(map_exchange_error)?,
            async_http: AsyncPublicHttpClient::new("kairos-integration/okx")
                .map_err(map_exchange_error)?,
        })
    }

    pub fn instrument_catalog(&self, instrument_type: InstrumentType) -> OkxInstrumentCatalog {
        OkxInstrumentCatalog {
            descriptor: self.public_descriptor(ConnectionDomain::MarketData, instrument_type),
            instrument_type,
            base_url: self.config.rest_base_url.clone(),
            http: self.async_http.clone(),
        }
    }

    pub fn blocking_instrument_catalog(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxInstrumentCatalog {
        blocking::OkxInstrumentCatalog {
            descriptor: self.public_descriptor(ConnectionDomain::MarketData, instrument_type),
            instrument_type,
            base_url: self.config.rest_base_url.clone(),
            http: self.blocking_http.clone(),
        }
    }

    pub fn market_snapshot(&self, instrument_type: InstrumentType) -> OkxMarketSnapshot {
        OkxMarketSnapshot {
            descriptor: self.public_descriptor(ConnectionDomain::MarketData, instrument_type),
            base_url: self.config.rest_base_url.clone(),
            http: self.async_http.clone(),
        }
    }

    pub fn live_market(
        &self,
        websocket_url: impl Into<String>,
    ) -> Result<OkxLiveMarket, IntegrationError> {
        OkxLiveMarket::new(
            self.public_descriptor(ConnectionDomain::MarketData, InstrumentType::Spot),
            websocket_url.into(),
        )
    }

    fn public_descriptor(
        &self,
        domain: ConnectionDomain,
        instrument_type: InstrumentType,
    ) -> ConnectionDescriptor {
        ConnectionDescriptor {
            binding_id: format!(
                "okx.public.{}.{}",
                domain.as_str(),
                instrument_type.as_str()
            ),
            participant: ParticipantRef::new(ParticipantKind::Exchange, "okx")
                .expect("static OKX participant"),
            environment: self.config.environment.clone(),
            principal_id: None,
            domain: domain.into(),
        }
    }

    pub fn principal_connection(
        &self,
        config: OkxPrincipalConfig,
    ) -> Result<OkxPrincipalConnection, IntegrationError> {
        let binding_id = config.binding_id.trim().to_owned();
        if binding_id.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX principal binding id is required".into(),
            ));
        }
        if config
            .principal_id
            .as_ref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(IntegrationError::InvalidRequest(
                "OKX principal id cannot be empty".into(),
            ));
        }
        let private_query_quota = match config.quota {
            Some(allocation) => {
                if allocation.private_requests_per_two_seconds == 0 {
                    return Err(IntegrationError::InvalidRequest(
                        "OKX private request quota must be positive".into(),
                    ));
                }
                let shared = self.config.shared_quota.as_ref().ok_or_else(|| {
                    IntegrationError::InvalidRequest(
                        "OKX principal quota requires a shared quota ledger".into(),
                    )
                })?;
                let principal_id = config.principal_id.as_deref().ok_or_else(|| {
                    IntegrationError::InvalidRequest(
                        "OKX principal quota requires principal_id".into(),
                    )
                })?;
                Some(Arc::new(
                    SharedFixedWindowQuota::open_or_register(
                        &shared.ledger_path,
                        &format!(
                            "okx:{}:egress:{}:principal:{}:private-query-2s",
                            self.config.environment,
                            shared.egress_scope_id.trim(),
                            principal_id.trim()
                        ),
                        allocation.private_requests_per_two_seconds,
                        0,
                        2_000,
                    )
                    .map_err(|error| {
                        IntegrationError::Unavailable(format!(
                            "open OKX shared quota ledger: {error}"
                        ))
                    })?,
                ))
            }
            None => None,
        };
        let private_order_quota = match config.order_quota {
            Some(allocation) => {
                if allocation.requests_per_two_seconds == 0
                    || allocation.cancel_reserve >= allocation.requests_per_two_seconds
                {
                    return Err(IntegrationError::InvalidRequest(
                        "OKX order quota must be positive and cancel_reserve must be smaller than the limit"
                            .into(),
                    ));
                }
                let shared = self.config.shared_quota.as_ref().ok_or_else(|| {
                    IntegrationError::InvalidRequest(
                        "OKX order quota requires a shared quota ledger".into(),
                    )
                })?;
                let principal_id = config.principal_id.as_deref().ok_or_else(|| {
                    IntegrationError::InvalidRequest("OKX order quota requires principal_id".into())
                })?;
                Some(Arc::new(
                    SharedFixedWindowQuota::open_or_register(
                        &shared.ledger_path,
                        &format!(
                            "okx:{}:egress:{}:principal:{}:private-order-2s",
                            self.config.environment,
                            shared.egress_scope_id.trim(),
                            principal_id.trim()
                        ),
                        allocation.requests_per_two_seconds,
                        allocation.cancel_reserve,
                        2_000,
                    )
                    .map_err(|error| {
                        IntegrationError::Unavailable(format!(
                            "open OKX shared order quota ledger: {error}"
                        ))
                    })?,
                ))
            }
            None => None,
        };
        let client = OkxAccountClient::from_shared_http_instrument(
            InstrumentType::Spot.api_value(),
            self.blocking_http.clone(),
            self.async_http.clone(),
            config.api_key.expose_secret().to_owned(),
            config.secret.expose_secret().to_owned(),
            config.passphrase.expose_secret().to_owned(),
            self.config.rest_base_url.clone(),
        )
        .map_err(map_exchange_error)?;
        Ok(OkxPrincipalConnection {
            binding_id,
            participant: ParticipantRef::new(ParticipantKind::Exchange, "okx")
                .expect("static OKX participant"),
            environment: self.config.environment.clone(),
            principal_id: config.principal_id,
            client,
            private_query_quota,
            private_order_quota,
            api_key: config.api_key,
            secret: config.secret,
            passphrase: config.passphrase,
        })
    }
}

impl OkxPrincipalConnection {
    pub fn trading_descriptor(&self, instrument_type: InstrumentType) -> ConnectionDescriptor {
        self.descriptor(ConnectionDomain::Trading, Some(instrument_type))
    }

    pub fn trading_account(&self, instrument_type: InstrumentType) -> OkxTradingAccountRead {
        OkxTradingAccountRead {
            descriptor: self.trading_descriptor(instrument_type),
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn trading_credential_inspection(
        &self,
        instrument_type: InstrumentType,
    ) -> OkxTradingCredentialInspection {
        OkxTradingCredentialInspection {
            descriptor: self.trading_descriptor(instrument_type),
            instrument_type,
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn trading_account_market_profile(
        &self,
        instrument_type: InstrumentType,
    ) -> OkxTradingAccountMarketProfile {
        OkxTradingAccountMarketProfile {
            descriptor: self.trading_descriptor(instrument_type),
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn trading_account_events(
        &self,
        instrument_type: InstrumentType,
        segment_key: impl Into<String>,
        config: &OkxPrivateChannelConfig,
    ) -> Result<OkxTradingAccountEvents, IntegrationError> {
        let segment_key = segment_key.into();
        if segment_key.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX account event segment key is required".into(),
            ));
        }
        let websocket_url = config.websocket_url.trim_end_matches('/').to_owned();
        if !(websocket_url.starts_with("wss://") || websocket_url.starts_with("ws://")) {
            return Err(IntegrationError::InvalidRequest(
                "OKX private WebSocket endpoint must start with ws:// or wss://".into(),
            ));
        }
        if config.event_queue_capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "OKX private event queue capacity must be positive".into(),
            ));
        }
        Ok(OkxTradingAccountEvents {
            descriptor: self.trading_descriptor(instrument_type),
            instrument_type,
            segment_key,
            websocket_url,
            event_queue_capacity: config.event_queue_capacity,
            api_key: self.api_key.clone(),
            secret: self.secret.clone(),
            passphrase: self.passphrase.clone(),
            socket: None,
            lifecycle: ConnectionLifecycle::Created,
            last_error: None,
            reconnect_count: 0,
            channel_epoch: 0,
            pending_messages: Default::default(),
        })
    }

    pub fn trading_order_entry(
        &self,
        instrument_type: InstrumentType,
        trading_mode: TradingMode,
    ) -> Result<OkxTradingOrderEntry, IntegrationError> {
        validate_trading_mode(instrument_type, trading_mode)?;
        let mut descriptor = self.trading_descriptor(instrument_type);
        descriptor.binding_id = format!("{}.{}", descriptor.binding_id, trading_mode.as_str());
        Ok(OkxTradingOrderEntry {
            descriptor,
            trading_mode,
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_order_quota.clone(),
        })
    }

    pub fn trading_order_query(&self, instrument_type: InstrumentType) -> OkxTradingOrderQuery {
        OkxTradingOrderQuery {
            descriptor: self.trading_descriptor(instrument_type),
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn trading_order_events(
        &self,
        instrument_type: InstrumentType,
        trading_mode: TradingMode,
        config: &OkxPrivateChannelConfig,
    ) -> Result<OkxTradingOrderEvents, IntegrationError> {
        validate_trading_mode(instrument_type, trading_mode)?;
        let (websocket_url, event_queue_capacity) = validate_private_channel_config(config)?;
        let mut descriptor = self.trading_descriptor(instrument_type);
        descriptor.binding_id = format!("{}.{}", descriptor.binding_id, trading_mode.as_str());
        Ok(OkxTradingOrderEvents {
            channel_id: format!("{}.order-events", descriptor.binding_id),
            descriptor,
            instrument_type,
            trading_mode,
            websocket_url,
            event_queue_capacity,
            api_key: self.api_key.clone(),
            secret: self.secret.clone(),
            passphrase: self.passphrase.clone(),
            socket: None,
            pending: VecDeque::new(),
            lifecycle: ConnectionLifecycle::Created,
            channel_epoch: 0,
            last_error: None,
        })
    }

    pub fn blocking_trading_account(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxTradingAccountRead {
        blocking::OkxTradingAccountRead {
            descriptor: self.trading_descriptor(instrument_type),
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn blocking_trading_credential_inspection(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxTradingCredentialInspection {
        blocking::OkxTradingCredentialInspection {
            descriptor: self.trading_descriptor(instrument_type),
            instrument_type,
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn blocking_trading_account_market_profile(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxTradingAccountMarketProfile {
        blocking::OkxTradingAccountMarketProfile {
            descriptor: self.trading_descriptor(instrument_type),
            client: self
                .client
                .clone()
                .with_instrument_type(instrument_type.api_value()),
            quota: self.private_query_quota.clone(),
        }
    }

    pub fn blocking_trading_account_events(
        &self,
        instrument_type: InstrumentType,
        segment_key: impl Into<String>,
        config: &OkxPrivateChannelConfig,
    ) -> Result<blocking::OkxTradingAccountEvents, IntegrationError> {
        let events = self.trading_account_events(instrument_type, segment_key, config)?;
        Ok(blocking::OkxTradingAccountEvents {
            descriptor: events.descriptor,
            instrument_type: events.instrument_type,
            segment_key: events.segment_key,
            websocket_url: events.websocket_url,
            event_queue_capacity: events.event_queue_capacity,
            api_key: events.api_key,
            secret: events.secret,
            passphrase: events.passphrase,
            socket: None,
            lifecycle: ConnectionLifecycle::Created,
            last_error: None,
            reconnect_count: 0,
        })
    }

    pub fn blocking_trading_order_entry(
        &self,
        instrument_type: InstrumentType,
        trading_mode: TradingMode,
    ) -> Result<blocking::OkxTradingOrderEntry, IntegrationError> {
        let entry = self.trading_order_entry(instrument_type, trading_mode)?;
        Ok(blocking::OkxTradingOrderEntry {
            descriptor: entry.descriptor,
            trading_mode: entry.trading_mode,
            client: entry.client,
            quota: entry.quota,
        })
    }

    pub fn blocking_trading_order_query(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxTradingOrderQuery {
        let query = self.trading_order_query(instrument_type);
        blocking::OkxTradingOrderQuery {
            descriptor: query.descriptor,
            client: query.client,
            quota: query.quota,
        }
    }

    fn descriptor(
        &self,
        domain: ConnectionDomain,
        instrument_type: Option<InstrumentType>,
    ) -> ConnectionDescriptor {
        let suffix = instrument_type
            .map(InstrumentType::as_str)
            .map(|value| format!(".{}.{value}", domain.as_str()))
            .unwrap_or_else(|| format!(".{}", domain.as_str()));
        ConnectionDescriptor {
            binding_id: format!("{}{}", self.binding_id, suffix),
            participant: self.participant.clone(),
            environment: self.environment.clone(),
            principal_id: self.principal_id.clone(),
            domain: domain.into(),
        }
    }
}

fn validate_trading_mode(
    instrument_type: InstrumentType,
    trading_mode: TradingMode,
) -> Result<(), IntegrationError> {
    let valid = match instrument_type {
        InstrumentType::Spot => trading_mode == TradingMode::Cash,
        InstrumentType::Margin => {
            matches!(trading_mode, TradingMode::Cross | TradingMode::Isolated)
        }
        InstrumentType::Swap | InstrumentType::Futures | InstrumentType::Option => {
            matches!(trading_mode, TradingMode::Cross | TradingMode::Isolated)
        }
    };
    if valid {
        Ok(())
    } else {
        Err(IntegrationError::InvalidRequest(format!(
            "OKX instType={} cannot use tdMode={}",
            instrument_type.as_str(),
            trading_mode.as_str()
        )))
    }
}

fn validate_private_channel_config(
    config: &OkxPrivateChannelConfig,
) -> Result<(String, usize), IntegrationError> {
    let websocket_url = config.websocket_url.trim_end_matches('/').to_owned();
    if !(websocket_url.starts_with("wss://") || websocket_url.starts_with("ws://")) {
        return Err(IntegrationError::InvalidRequest(
            "OKX private WebSocket endpoint must start with ws:// or wss://".into(),
        ));
    }
    if config.event_queue_capacity == 0 {
        return Err(IntegrationError::InvalidRequest(
            "OKX private event queue capacity must be positive".into(),
        ));
    }
    Ok((websocket_url, config.event_queue_capacity))
}

fn acquire(quota: Option<&SharedFixedWindowQuota>, weight: u32) -> Result<(), IntegrationError> {
    acquire_with_priority(quota, weight, SharedQuotaPriority::Ordinary)
}

fn acquire_with_priority(
    quota: Option<&SharedFixedWindowQuota>,
    weight: u32,
    priority: SharedQuotaPriority,
) -> Result<(), IntegrationError> {
    quota
        .map(|quota| {
            quota
                .acquire(weight, priority, now_millis())
                .map_err(map_quota_exhausted)
        })
        .transpose()
        .map(|_| ())
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn map_quota_exhausted(error: SharedQuotaExhausted) -> IntegrationError {
    IntegrationError::RateLimited(format!(
        "OKX shared private-query quota exhausted; retry after {}ms",
        error.retry_after_millis
    ))
}

fn map_exchange_error(error: ExchangeError) -> IntegrationError {
    match error {
        ExchangeError::Authentication(message) => IntegrationError::Authentication(message),
        ExchangeError::InvalidRequest(message) => IntegrationError::InvalidRequest(message),
        ExchangeError::LocalRateLimit { message, .. } => IntegrationError::RateLimited(message),
        ExchangeError::Preflight(message) => IntegrationError::Unavailable(message),
        ExchangeError::Http { status: 401, body } => IntegrationError::Authentication(body),
        ExchangeError::Http { status: 403, body } => IntegrationError::Authorization(body),
        ExchangeError::Http { status: 429, body } => IntegrationError::RateLimited(body),
        other => IntegrationError::Transport(other.to_string()),
    }
}

#[cfg(test)]
mod tests;
