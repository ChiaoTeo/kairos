//! OKX participant-native connection facade and capability projections.

use std::collections::VecDeque;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::Utc;
use kairos_domain_types::{Currency, Price, ProviderSymbol, Quantity, Symbol, UnixNanos};
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
    ExternalOrderQuery, IntegrationError, MarketEvent, MarketEventKind, MarketSnapshotConnection,
    OrderEntryConnection, OrderQueryConnection,
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

pub struct OkxTradingAccountRead {
    descriptor: ConnectionDescriptor,
    client: OkxAccountClient,
    quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingCredentialInspection {
    descriptor: ConnectionDescriptor,
    instrument_type: InstrumentType,
    client: OkxAccountClient,
    quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingAccountMarketProfile {
    descriptor: ConnectionDescriptor,
    client: OkxAccountClient,
    quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingAccountEvents {
    descriptor: ConnectionDescriptor,
    instrument_type: InstrumentType,
    segment_key: String,
    websocket_url: String,
    event_queue_capacity: usize,
    api_key: SecretString,
    secret: SecretString,
    passphrase: SecretString,
    socket: Option<AsyncTokioSocket>,
    lifecycle: ConnectionLifecycle,
    last_error: Option<String>,
    reconnect_count: u64,
}

pub struct OkxTradingOrderEntry {
    descriptor: ConnectionDescriptor,
    trading_mode: TradingMode,
    client: OkxAccountClient,
    quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingOrderQuery {
    descriptor: ConnectionDescriptor,
    client: OkxAccountClient,
    quota: Option<Arc<SharedFixedWindowQuota>>,
}

pub struct OkxTradingOrderEvents {
    descriptor: ConnectionDescriptor,
    channel_id: String,
    instrument_type: InstrumentType,
    trading_mode: TradingMode,
    websocket_url: String,
    event_queue_capacity: usize,
    api_key: SecretString,
    secret: SecretString,
    passphrase: SecretString,
    socket: Option<AsyncTokioSocket>,
    pending: VecDeque<ExternalEventEnvelope<ExternalExecutionEvent>>,
    lifecycle: ConnectionLifecycle,
    channel_epoch: u64,
    last_error: Option<String>,
}

pub struct OkxInstrumentCatalog {
    descriptor: ConnectionDescriptor,
    instrument_type: InstrumentType,
    base_url: String,
    http: AsyncPublicHttpClient,
}

pub struct OkxMarketSnapshot {
    descriptor: ConnectionDescriptor,
    base_url: String,
    http: AsyncPublicHttpClient,
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

    pub fn blocking_market_snapshot(
        &self,
        instrument_type: InstrumentType,
    ) -> blocking::OkxMarketSnapshot {
        blocking::OkxMarketSnapshot {
            descriptor: self.public_descriptor(ConnectionDomain::MarketData, instrument_type),
            base_url: self.config.rest_base_url.clone(),
            http: self.blocking_http.clone(),
        }
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

fn normalize_instrument_catalog(
    instrument_type: InstrumentType,
    payload: &serde_json::Value,
) -> Result<ExternalInstrumentCatalog, IntegrationError> {
    if payload.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(IntegrationError::InvalidPayload(format!(
            "OKX instrument catalog request failed: {payload}"
        )));
    }
    let rows = payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX instruments response data is missing".into())
        })?;
    fn optional_text<'a>(row: &'a serde_json::Value, field: &str) -> Option<&'a str> {
        row.get(field)
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    }
    let instruments = rows
        .iter()
        .map(|row| {
            let source_symbol = optional_text(row, "instId").ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX instrument id is missing".into())
            })?;
            let expiry_unix_nanos = optional_text(row, "expTime")
                .and_then(|value| value.parse::<u64>().ok())
                .filter(|value| *value != 0)
                .map(|value| UnixNanos::from(value.saturating_mul(1_000_000)));
            let currency = |field| {
                optional_text(row, field)
                    .map(Currency::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
            };
            Ok(ExternalInstrument {
                source_symbol: ProviderSymbol::new(source_symbol)
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                source_venue: None,
                kind: match instrument_type {
                    InstrumentType::Spot => ExternalInstrumentKind::Spot,
                    InstrumentType::Margin => ExternalInstrumentKind::Margin,
                    InstrumentType::Swap => ExternalInstrumentKind::Perpetual,
                    InstrumentType::Futures => ExternalInstrumentKind::Future,
                    InstrumentType::Option => ExternalInstrumentKind::Option,
                },
                base_currency: currency("baseCcy")?,
                quote_currency: currency("quoteCcy")?,
                settlement_currency: currency("settleCcy")?,
                underlying: optional_text(row, "uly")
                    .map(ProviderSymbol::new)
                    .transpose()
                    .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
                expiry_unix_nanos,
                strike: optional_text(row, "stk").map(str::to_owned),
                option_right: optional_text(row, "optType").map(str::to_owned),
                active: optional_text(row, "state") == Some("live"),
                price_tick: optional_text(row, "tickSz").map(str::to_owned),
                quantity_tick: optional_text(row, "lotSz").map(str::to_owned),
                minimum_quantity: optional_text(row, "minSz").map(str::to_owned),
                minimum_notional: None,
                contract_value: optional_text(row, "ctVal").map(str::to_owned),
                price_precision: None,
                quantity_precision: None,
            })
        })
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(ExternalInstrumentCatalog {
        participant: ParticipantRef::new(ParticipantKind::Exchange, "okx")
            .expect("static OKX participant"),
        instruments,
    })
}

fn normalize_market_snapshot(
    requested: &ProviderSymbol,
    payload: &serde_json::Value,
) -> Result<MarketEvent, IntegrationError> {
    if payload.get("code").and_then(serde_json::Value::as_str) != Some("0") {
        return Err(IntegrationError::InvalidPayload(format!(
            "OKX ticker request failed: {payload}"
        )));
    }
    let row = payload
        .get("data")
        .and_then(serde_json::Value::as_array)
        .and_then(|rows| rows.first())
        .ok_or_else(|| IntegrationError::InvalidPayload("OKX ticker has no data".into()))?;
    let text = |field| {
        row.get(field)
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    };
    let bid = parse_optional_ticker::<Price>(row, "bidPx")?;
    let ask = parse_optional_ticker::<Price>(row, "askPx")?;
    if bid.is_none() && ask.is_none() {
        return Err(IntegrationError::InvalidPayload(
            "OKX ticker has neither bid nor ask".into(),
        ));
    }
    Ok(MarketEvent {
        symbol: Symbol::new(text("instId").unwrap_or(requested.as_str()))
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        kind: MarketEventKind::Quote,
        price: bid,
        quantity: parse_optional_ticker::<Quantity>(row, "bidSz")?,
        rate: None,
        ask_price: ask,
        ask_quantity: parse_optional_ticker::<Quantity>(row, "askSz")?,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: now_unix_nanos().into(),
    })
}

fn parse_optional_ticker<T>(
    row: &serde_json::Value,
    field: &str,
) -> Result<Option<T>, IntegrationError>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    row.get(field)
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::parse)
        .transpose()
        .map_err(|error| IntegrationError::InvalidPayload(format!("OKX ticker {field}: {error}")))
}

impl OkxInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for OkxInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let payload = self
            .http
            .get_json_response_with_headers_and_query(
                &format!("{}/api/v5/public/instruments", self.base_url),
                &[("instType", self.instrument_type.api_value().into())],
                &[],
            )
            .await
            .map_err(map_exchange_error)?
            .body;
        normalize_instrument_catalog(self.instrument_type, &payload)
    }
}

impl OkxMarketSnapshot {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncMarketSnapshotConnection for OkxMarketSnapshot {
    async fn fetch_snapshot(
        &mut self,
        symbols: &[ProviderSymbol],
    ) -> Result<Vec<MarketEvent>, IntegrationError> {
        let mut events = Vec::with_capacity(symbols.len());
        for symbol in symbols {
            let payload = self
                .http
                .get_json_response_with_headers_and_query(
                    &format!("{}/api/v5/market/ticker", self.base_url),
                    &[("instId", symbol.as_str().to_owned())],
                    &[],
                )
                .await
                .map_err(map_exchange_error)?
                .body;
            events.push(normalize_market_snapshot(symbol, &payload)?);
        }
        Ok(events)
    }
}

impl OkxTradingAccountEvents {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn fail(&mut self, message: impl Into<String>) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(message.into());
    }
}

impl AsyncAccountEventSource for OkxTradingAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        self.last_error = None;
        let mut socket = AsyncTokioSocket::connect(&self.websocket_url, self.event_queue_capacity)
            .await
            .map_err(|error| {
                self.fail(error.clone());
                IntegrationError::Transport(error)
            })?;
        let timestamp = Utc::now().timestamp().to_string();
        let sign = okx_signature(
            self.secret.expose_secret(),
            &timestamp,
            "GET",
            "/users/self/verify",
            "",
        )
        .map_err(map_exchange_error)?;
        socket
            .send_text(
                serde_json::json!({
                    "op": "login",
                    "args": [{
                        "apiKey": self.api_key.expose_secret(),
                        "passphrase": self.passphrase.expose_secret(),
                        "timestamp": timestamp,
                        "sign": sign
                    }]
                })
                .to_string(),
            )
            .await
            .map_err(IntegrationError::Transport)?;
        let login = match socket.next_event().await {
            AsyncSocketEvent::Message(message) => message,
            AsyncSocketEvent::Error(error) => {
                self.fail(error.clone());
                return Err(IntegrationError::Transport(error));
            }
            AsyncSocketEvent::Backpressure => {
                self.fail("OKX private stream queue overflowed during login");
                return Err(IntegrationError::Backpressure(
                    "OKX private stream queue overflowed during login".into(),
                ));
            }
        };
        if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
            self.fail("OKX private stream login was rejected");
            return Err(IntegrationError::Authentication(
                "OKX private stream login was rejected".into(),
            ));
        }
        socket
            .send_text(
                serde_json::json!({
                    "op": "subscribe",
                    "args": [
                        {"channel": "account"},
                        {"channel": "orders", "instType": self.instrument_type.api_value()}
                    ]
                })
                .to_string(),
            )
            .await
            .map_err(IntegrationError::Transport)?;
        self.socket = Some(socket);
        self.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.disconnect_channel().await?;
        self.reconnect_count = self.reconnect_count.saturating_add(1);
        self.connect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.lifecycle == ConnectionLifecycle::Ready,
            last_error: self.last_error.clone(),
        }
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEvent,
        IntegrationError,
    > {
        loop {
            let event = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await;
            match event {
                AsyncSocketEvent::Message(Message::Text(text)) => {
                    if let Some(event) = parse_event(&self.segment_key, &text)
                        .map_err(IntegrationError::InvalidPayload)?
                    {
                        return Ok(event);
                    }
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => {
                    self.socket
                        .as_ref()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .await
                        .map_err(IntegrationError::Transport)?;
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(error) => {
                    self.fail(error.clone());
                    return Err(IntegrationError::Transport(error));
                }
                AsyncSocketEvent::Backpressure => {
                    self.fail("OKX private stream event queue overflowed");
                    return Err(IntegrationError::Backpressure(
                        "OKX private stream event queue overflowed".into(),
                    ));
                }
            }
        }
    }
}

impl OkxTradingOrderEvents {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn fail(&mut self, error: &IntegrationError) {
        self.lifecycle = ConnectionLifecycle::Degraded;
        self.last_error = Some(error.to_string());
    }
}

impl AsyncOrderEventSource for OkxTradingOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.lifecycle = ConnectionLifecycle::Starting;
        self.last_error = None;
        self.pending.clear();
        let mut socket =
            match AsyncTokioSocket::connect(&self.websocket_url, self.event_queue_capacity).await {
                Ok(socket) => socket,
                Err(message) => {
                    let error = IntegrationError::Transport(message);
                    self.fail(&error);
                    return Err(error);
                }
            };
        let timestamp = Utc::now().timestamp().to_string();
        let sign = okx_signature(
            self.secret.expose_secret(),
            &timestamp,
            "GET",
            "/users/self/verify",
            "",
        )
        .map_err(map_exchange_error)?;
        if let Err(message) = socket
            .send_text(
                serde_json::json!({
                    "op": "login",
                    "args": [{
                        "apiKey": self.api_key.expose_secret(),
                        "passphrase": self.passphrase.expose_secret(),
                        "timestamp": timestamp,
                        "sign": sign
                    }]
                })
                .to_string(),
            )
            .await
        {
            let error = IntegrationError::Transport(message);
            self.fail(&error);
            return Err(error);
        }
        let login = match socket.next_event().await {
            AsyncSocketEvent::Message(message) => message,
            AsyncSocketEvent::Error(message) => {
                let error = IntegrationError::Transport(message);
                self.fail(&error);
                return Err(error);
            }
            AsyncSocketEvent::Backpressure => {
                let error = IntegrationError::Backpressure(
                    "OKX order stream queue overflowed during login".into(),
                );
                self.fail(&error);
                return Err(error);
            }
        };
        if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
            let error = IntegrationError::Authentication(
                "OKX private order stream login was rejected".into(),
            );
            self.fail(&error);
            return Err(error);
        }
        if let Err(message) = socket
            .send_text(
                serde_json::json!({
                    "op": "subscribe",
                    "args": [{
                        "channel": "orders",
                        "instType": self.instrument_type.api_value()
                    }]
                })
                .to_string(),
            )
            .await
        {
            let error = IntegrationError::Transport(message);
            self.fail(&error);
            return Err(error);
        }
        self.socket = Some(socket);
        self.channel_epoch = self.channel_epoch.saturating_add(1);
        self.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.lifecycle = ConnectionLifecycle::Stopping;
        if let Some(mut socket) = self.socket.take() {
            socket.close().await;
        }
        self.pending.clear();
        self.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.lifecycle,
            healthy: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            authenticated: self.lifecycle == ConnectionLifecycle::Ready && self.socket.is_some(),
            last_error: self.last_error.clone(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.connect_channel().await?;
        loop {
            if let Some(event) = self.pending.pop_front() {
                return Ok(event);
            }
            let event = self
                .socket
                .as_mut()
                .ok_or(IntegrationError::NotReady)?
                .next_event()
                .await;
            match event {
                AsyncSocketEvent::Message(Message::Text(text)) => {
                    let events = parse_execution_events(
                        &self.descriptor.binding_id,
                        &self.channel_id,
                        self.channel_epoch,
                        self.trading_mode.as_str(),
                        now_unix_nanos().into(),
                        &text,
                    )
                    .map_err(IntegrationError::InvalidPayload)?;
                    self.pending.extend(events);
                }
                AsyncSocketEvent::Message(Message::Ping(payload)) => self
                    .socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .send_pong(payload.to_vec())
                    .await
                    .map_err(IntegrationError::Transport)?,
                AsyncSocketEvent::Message(Message::Close(frame)) => {
                    let error = IntegrationError::ResyncRequired(format!(
                        "OKX private order stream closed: {frame:?}"
                    ));
                    self.fail(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Message(_) => {}
                AsyncSocketEvent::Error(message) => {
                    let error = IntegrationError::Transport(message);
                    self.fail(&error);
                    return Err(error);
                }
                AsyncSocketEvent::Backpressure => {
                    let error = IntegrationError::Backpressure(
                        "OKX private order stream queue overflowed; reconciliation is required"
                            .into(),
                    );
                    self.fail(&error);
                    return Err(error);
                }
            }
        }
    }
}

impl OkxTradingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountReadConnection for OkxTradingAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        acquire(self.quota.as_deref(), 3)?;
        let (balance, positions, orders) = tokio::try_join!(
            self.client.balance_async(),
            self.client.positions_async(),
            self.client.pending_orders_async(),
        )
        .map_err(map_exchange_error)?;
        normalize_account(segment, &balance, &positions, &orders)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountCredentialInspectionConnection for OkxTradingCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .config_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_credential_profile(&payload, self.instrument_type.as_str())
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingAccountMarketProfile {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncAccountMarketProfileConnection for OkxTradingAccountMarketProfile {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "OKX market profile instrument is required".into(),
            ));
        }
        acquire(self.quota.as_deref(), 2)?;
        let (fee, config) = tokio::try_join!(
            self.client.trade_fee_async(request.source_symbol.as_str()),
            self.client.config_async(),
        )
        .map_err(map_exchange_error)?;
        normalize_market_profile(request, &fee, &config).map_err(IntegrationError::InvalidPayload)
    }
}

impl OkxTradingOrderEntry {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncOrderEntryConnection for OkxTradingOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Ordinary)?;
        let body = order_request_body(request, self.trading_mode.as_str())?;
        let payload = match self.client.submit_order_async(body).await {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_submission(request, &payload)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Reserved)?;
        let body = cancel_request_body(request, remote_order_id)?;
        let payload = match self.client.cancel_order_async(body).await {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        normalize_order_cancellation(request, remote_order_id, at_unix_nanos, &payload)
    }
}

impl OkxTradingOrderQuery {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    fn stamp(&self, mut orders: Vec<ExternalOrder>) -> Vec<ExternalOrder> {
        for order in &mut orders {
            order.binding_id.clone_from(&self.descriptor.binding_id);
        }
        orders
    }
}

impl AsyncOrderQueryConnection for OkxTradingOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_open_async(query)
            .await
            .map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_history_async(query)
            .await
            .map_err(map_exchange_error)?;
        normalize_okx_orders(&payload)
            .map(|orders| self.stamp(orders))
            .map_err(IntegrationError::InvalidPayload)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        acquire(self.quota.as_deref(), 1)?;
        let payload = self
            .client
            .order_detail_async(query)
            .await
            .map_err(map_exchange_error)?;
        let rows = payload
            .get("data")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX order detail data is missing".into())
            })?;
        rows.first()
            .map(normalize_okx_order)
            .transpose()
            .map(|order| {
                order.map(|mut order| {
                    order.binding_id.clone_from(&self.descriptor.binding_id);
                    order
                })
            })
            .map_err(IntegrationError::InvalidPayload)
    }
}

pub mod blocking {
    use super::*;
    use crate::services::transport::websocket::{SocketEvent, TokioSocket};

    pub struct OkxTradingAccountRead {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: OkxAccountClient,
        pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
    }

    pub struct OkxTradingCredentialInspection {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) instrument_type: InstrumentType,
        pub(super) client: OkxAccountClient,
        pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
    }

    pub struct OkxTradingAccountMarketProfile {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: OkxAccountClient,
        pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
    }

    pub struct OkxTradingAccountEvents {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) instrument_type: InstrumentType,
        pub(super) segment_key: String,
        pub(super) websocket_url: String,
        pub(super) event_queue_capacity: usize,
        pub(super) api_key: SecretString,
        pub(super) secret: SecretString,
        pub(super) passphrase: SecretString,
        pub(super) socket: Option<TokioSocket>,
        pub(super) lifecycle: ConnectionLifecycle,
        pub(super) last_error: Option<String>,
        pub(super) reconnect_count: u64,
    }

    pub struct OkxTradingOrderEntry {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) trading_mode: TradingMode,
        pub(super) client: OkxAccountClient,
        pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
    }

    pub struct OkxTradingOrderQuery {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) client: OkxAccountClient,
        pub(super) quota: Option<Arc<SharedFixedWindowQuota>>,
    }

    pub struct OkxInstrumentCatalog {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) instrument_type: InstrumentType,
        pub(super) base_url: String,
        pub(super) http: PublicHttpClient,
    }

    pub struct OkxMarketSnapshot {
        pub(super) descriptor: ConnectionDescriptor,
        pub(super) base_url: String,
        pub(super) http: PublicHttpClient,
    }

    fn reject_async_runtime() -> Result<(), IntegrationError> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(IntegrationError::InvalidRequest(
                "kairos_integration::blocking cannot run on a Tokio runtime worker; use the default async capability or move the blocking call to a dedicated thread".into(),
            ));
        }
        Ok(())
    }

    impl OkxInstrumentCatalog {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl InstrumentCatalogConnection for OkxInstrumentCatalog {
        fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
            reject_async_runtime()?;
            let payload = self
                .http
                .get_json_with_query(
                    &format!("{}/api/v5/public/instruments", self.base_url),
                    &[("instType", self.instrument_type.api_value().into())],
                )
                .map_err(map_exchange_error)?;
            normalize_instrument_catalog(self.instrument_type, &payload)
        }
    }

    impl OkxMarketSnapshot {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl MarketSnapshotConnection for OkxMarketSnapshot {
        fn fetch_snapshot(
            &mut self,
            symbols: &[ProviderSymbol],
        ) -> Result<Vec<MarketEvent>, IntegrationError> {
            reject_async_runtime()?;
            symbols
                .iter()
                .map(|symbol| {
                    let payload = self
                        .http
                        .get_json_with_query(
                            &format!("{}/api/v5/market/ticker", self.base_url),
                            &[("instId", symbol.as_str().to_owned())],
                        )
                        .map_err(map_exchange_error)?;
                    normalize_market_snapshot(symbol, &payload)
                })
                .collect()
        }
    }

    impl OkxTradingAccountRead {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn fetch_account(
            &mut self,
            segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
        ) -> Result<
            crate::application::capabilities::account_facts::ExternalAccountSnapshot,
            IntegrationError,
        > {
            reject_async_runtime()?;
            acquire(self.quota.as_deref(), 3)?;
            let balance = self.client.balance().map_err(map_exchange_error)?;
            let positions = self.client.positions().map_err(map_exchange_error)?;
            let orders = self.client.pending_orders().map_err(map_exchange_error)?;
            normalize_account(segment, &balance, &positions, &orders)
                .map_err(IntegrationError::InvalidPayload)
        }
    }

    impl OkxTradingCredentialInspection {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn inspect_credential(
            &mut self,
        ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
            reject_async_runtime()?;
            acquire(self.quota.as_deref(), 1)?;
            let payload = self.client.config().map_err(map_exchange_error)?;
            normalize_credential_profile(&payload, self.instrument_type.as_str())
                .map_err(IntegrationError::InvalidPayload)
        }
    }

    impl OkxTradingAccountMarketProfile {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn fetch_market_profile(
            &mut self,
            request: &ExternalMarketProfileRequest,
        ) -> Result<ExternalMarketProfile, IntegrationError> {
            reject_async_runtime()?;
            if request.source_symbol.as_str().trim().is_empty() {
                return Err(IntegrationError::InvalidRequest(
                    "OKX market profile instrument is required".into(),
                ));
            }
            acquire(self.quota.as_deref(), 2)?;
            let fee = self
                .client
                .trade_fee(request.source_symbol.as_str())
                .map_err(map_exchange_error)?;
            let config = self.client.config().map_err(map_exchange_error)?;
            normalize_market_profile(request, &fee, &config)
                .map_err(IntegrationError::InvalidPayload)
        }
    }

    impl OkxTradingOrderEntry {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl OrderEntryConnection for OkxTradingOrderEntry {
        fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent> {
            reject_async_runtime()?;
            acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Ordinary)?;
            let body = order_request_body(request, self.trading_mode.as_str())?;
            let payload = match self.client.submit_order(body) {
                Ok(payload) => payload,
                Err(error) => return command_error_outcome(error),
            };
            normalize_order_submission(request, &payload)
        }

        fn cancel_order(
            &mut self,
            request: &OrderEntryRequest,
            remote_order_id: &str,
            at_unix_nanos: u64,
        ) -> CommandResult<OrderEntryEvent> {
            reject_async_runtime()?;
            acquire_with_priority(self.quota.as_deref(), 1, SharedQuotaPriority::Reserved)?;
            let body = cancel_request_body(request, remote_order_id)?;
            let payload = match self.client.cancel_order(body) {
                Ok(payload) => payload,
                Err(error) => return command_error_outcome(error),
            };
            normalize_order_cancellation(request, remote_order_id, at_unix_nanos, &payload)
        }
    }

    impl OkxTradingOrderQuery {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        fn stamp(&self, mut orders: Vec<ExternalOrder>) -> Vec<ExternalOrder> {
            for order in &mut orders {
                order.binding_id.clone_from(&self.descriptor.binding_id);
            }
            orders
        }
    }

    impl OrderQueryConnection for OkxTradingOrderQuery {
        fn open_orders(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            acquire(self.quota.as_deref(), 1)?;
            let payload = self.client.order_open(query).map_err(map_exchange_error)?;
            normalize_okx_orders(&payload)
                .map(|orders| self.stamp(orders))
                .map_err(IntegrationError::InvalidPayload)
        }

        fn order_history(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            acquire(self.quota.as_deref(), 1)?;
            let payload = self
                .client
                .order_history(query)
                .map_err(map_exchange_error)?;
            normalize_okx_orders(&payload)
                .map(|orders| self.stamp(orders))
                .map_err(IntegrationError::InvalidPayload)
        }

        fn order_detail(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError> {
            reject_async_runtime()?;
            acquire(self.quota.as_deref(), 1)?;
            let payload = self
                .client
                .order_detail(query)
                .map_err(map_exchange_error)?;
            let rows = payload
                .get("data")
                .and_then(serde_json::Value::as_array)
                .ok_or_else(|| {
                    IntegrationError::InvalidPayload("OKX order detail data is missing".into())
                })?;
            rows.first()
                .map(normalize_okx_order)
                .transpose()
                .map(|order| {
                    order.map(|mut order| {
                        order.binding_id.clone_from(&self.descriptor.binding_id);
                        order
                    })
                })
                .map_err(IntegrationError::InvalidPayload)
        }
    }

    impl OkxTradingAccountEvents {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }

        pub fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            if self.socket.is_some() && self.lifecycle == ConnectionLifecycle::Ready {
                return Ok(());
            }
            self.lifecycle = ConnectionLifecycle::Starting;
            let socket = TokioSocket::connect_with_event_capacity(
                self.websocket_url.clone(),
                self.event_queue_capacity,
            )
            .map_err(IntegrationError::Transport)?;
            let timestamp = Utc::now().timestamp().to_string();
            let sign = okx_signature(
                self.secret.expose_secret(),
                &timestamp,
                "GET",
                "/users/self/verify",
                "",
            )
            .map_err(map_exchange_error)?;
            socket
                .send_text(
                    serde_json::json!({
                        "op": "login",
                        "args": [{
                            "apiKey": self.api_key.expose_secret(),
                            "passphrase": self.passphrase.expose_secret(),
                            "timestamp": timestamp,
                            "sign": sign
                        }]
                    })
                    .to_string(),
                )
                .map_err(IntegrationError::Transport)?;
            let login = socket.recv().map_err(IntegrationError::Transport)?;
            let login = match login {
                SocketEvent::Message(message) => message,
                SocketEvent::Error(error) => return Err(IntegrationError::Transport(error)),
                SocketEvent::Backpressure => {
                    return Err(IntegrationError::Backpressure(
                        "OKX private stream queue overflowed during login".into(),
                    ))
                }
            };
            if !login_succeeded(&login).map_err(IntegrationError::InvalidPayload)? {
                return Err(IntegrationError::Authentication(
                    "OKX private stream login was rejected".into(),
                ));
            }
            socket
                .send_text(
                    serde_json::json!({
                        "op": "subscribe",
                        "args": [
                            {"channel": "account"},
                            {"channel": "orders", "instType": self.instrument_type.api_value()}
                        ]
                    })
                    .to_string(),
                )
                .map_err(IntegrationError::Transport)?;
            self.socket = Some(socket);
            self.lifecycle = ConnectionLifecycle::Ready;
            self.last_error = None;
            Ok(())
        }

        pub fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            self.socket.take();
            self.lifecycle = ConnectionLifecycle::Stopped;
            Ok(())
        }

        pub fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            reject_async_runtime()?;
            self.socket.take();
            self.reconnect_count = self.reconnect_count.saturating_add(1);
            self.connect_channel()
        }

        pub fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: self.lifecycle,
                healthy: self.lifecycle == ConnectionLifecycle::Ready,
                authenticated: self.lifecycle == ConnectionLifecycle::Ready,
                last_error: self.last_error.clone(),
            }
        }

        pub fn next_account_event(
            &mut self,
        ) -> Result<
            crate::application::capabilities::account_facts::ExternalAccountEvent,
            IntegrationError,
        > {
            reject_async_runtime()?;
            loop {
                let event = self
                    .socket
                    .as_ref()
                    .ok_or(IntegrationError::NotReady)?
                    .recv()
                    .map_err(IntegrationError::Transport)?;
                match event {
                    SocketEvent::Message(Message::Text(text)) => {
                        if let Some(event) = parse_event(&self.segment_key, &text)
                            .map_err(IntegrationError::InvalidPayload)?
                        {
                            return Ok(event);
                        }
                    }
                    SocketEvent::Message(Message::Ping(payload)) => self
                        .socket
                        .as_ref()
                        .ok_or(IntegrationError::NotReady)?
                        .send_pong(payload.to_vec())
                        .map_err(IntegrationError::Transport)?,
                    SocketEvent::Message(_) => {}
                    SocketEvent::Error(error) => {
                        self.lifecycle = ConnectionLifecycle::Degraded;
                        self.last_error = Some(error.clone());
                        return Err(IntegrationError::Transport(error));
                    }
                    SocketEvent::Backpressure => {
                        self.lifecycle = ConnectionLifecycle::Degraded;
                        self.last_error = Some("OKX private stream event queue overflowed".into());
                        return Err(IntegrationError::Backpressure(
                            "OKX private stream event queue overflowed".into(),
                        ));
                    }
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        #[tokio::test]
        async fn blocking_facade_rejects_tokio_runtime_workers() {
            assert!(super::reject_async_runtime().is_err());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::{SinkExt, StreamExt};

    fn principal() -> OkxPrincipalConnection {
        OkxConnection::connect(OkxConnectionConfig {
            environment: "paper".into(),
            rest_base_url: "https://example.test".into(),
            shared_quota: None,
        })
        .unwrap()
        .principal_connection(OkxPrincipalConfig {
            binding_id: "account.okx.main".into(),
            principal_id: Some("principal-1".into()),
            api_key: "api-key".into(),
            secret: "secret".into(),
            passphrase: "passphrase".into(),
            quota: None,
            order_quota: None,
        })
        .unwrap()
    }

    fn provider() -> OkxConnection {
        OkxConnection::connect(OkxConnectionConfig {
            environment: "paper".into(),
            rest_base_url: "https://example.test".into(),
            shared_quota: None,
        })
        .unwrap()
    }

    #[test]
    fn trading_capabilities_share_participant_domain_without_capability_metadata() {
        let principal = principal();
        let account = principal.trading_account(InstrumentType::Spot);
        let inspection = principal.trading_credential_inspection(InstrumentType::Spot);
        let profile = principal.trading_account_market_profile(InstrumentType::Spot);
        assert_eq!(account.descriptor(), inspection.descriptor());
        assert_eq!(account.descriptor(), profile.descriptor());
        assert_eq!(account.descriptor().domain.as_str(), "trading");
        assert_eq!(account.descriptor().participant.id.as_str(), "okx");
    }

    #[test]
    fn instrument_type_is_not_connection_domain() {
        let principal = principal();
        let spot = principal.trading_descriptor(InstrumentType::Spot);
        let swap = principal.trading_descriptor(InstrumentType::Swap);
        assert_eq!(spot.domain, swap.domain);
        assert_ne!(spot.binding_id, swap.binding_id);
    }

    #[test]
    fn order_traits_are_the_capability_boundary_and_td_mode_is_validated() {
        fn is_async_entry<T: AsyncOrderEntryConnection>(_: &T) {}
        fn is_async_query<T: AsyncOrderQueryConnection>(_: &T) {}
        fn is_blocking_entry<T: OrderEntryConnection>(_: &T) {}
        fn is_blocking_query<T: OrderQueryConnection>(_: &T) {}

        let principal = principal();
        let entry = principal
            .trading_order_entry(InstrumentType::Spot, TradingMode::Cash)
            .unwrap();
        let query = principal.trading_order_query(InstrumentType::Spot);
        let blocking_entry = principal
            .blocking_trading_order_entry(InstrumentType::Spot, TradingMode::Cash)
            .unwrap();
        let blocking_query = principal.blocking_trading_order_query(InstrumentType::Spot);
        is_async_entry(&entry);
        is_async_query(&query);
        is_blocking_entry(&blocking_entry);
        is_blocking_query(&blocking_query);
        assert_eq!(entry.descriptor().domain.as_str(), "trading");
        assert!(principal
            .trading_order_entry(InstrumentType::Spot, TradingMode::Cross)
            .is_err());
        assert!(principal
            .trading_order_entry(InstrumentType::Margin, TradingMode::Cash)
            .is_err());
    }

    #[test]
    fn public_catalog_returns_provider_facts_without_canonical_identity() {
        fn is_async<T: AsyncInstrumentCatalogConnection>(_: &T) {}
        fn is_blocking<T: InstrumentCatalogConnection>(_: &T) {}
        fn is_async_snapshot<T: AsyncMarketSnapshotConnection>(_: &T) {}
        fn is_blocking_snapshot<T: MarketSnapshotConnection>(_: &T) {}
        let provider = provider();
        let catalog = provider.instrument_catalog(InstrumentType::Swap);
        let blocking = provider.blocking_instrument_catalog(InstrumentType::Swap);
        let snapshot = provider.market_snapshot(InstrumentType::Swap);
        let blocking_snapshot = provider.blocking_market_snapshot(InstrumentType::Swap);
        is_async(&catalog);
        is_blocking(&blocking);
        is_async_snapshot(&snapshot);
        is_blocking_snapshot(&blocking_snapshot);
        assert_eq!(catalog.descriptor().domain.as_str(), "market-data");
        assert!(catalog.descriptor().principal_id.is_none());
        assert_eq!(catalog.descriptor(), snapshot.descriptor());

        let facts = normalize_instrument_catalog(
            InstrumentType::Swap,
            &serde_json::json!({
                "code": "0",
                "data": [{
                    "instId": "BTC-USDT-SWAP",
                    "uly": "BTC-USDT",
                    "settleCcy": "USDT",
                    "state": "live",
                    "tickSz": "0.1",
                    "lotSz": "0.01",
                    "minSz": "0.01",
                    "ctVal": "0.01"
                }]
            }),
        )
        .unwrap();
        assert_eq!(facts.instruments[0].source_symbol, "BTC-USDT-SWAP");
        assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Perpetual);
        assert_eq!(
            facts.instruments[0].settlement_currency.as_deref(),
            Some("USDT")
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn public_catalog_uses_the_callers_runtime() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = vec![0_u8; 4096];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("GET /api/v5/public/instruments?instType=SWAP "));

            let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","uly":"BTC-USDT","settleCcy":"USDT","state":"live"}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).await.unwrap();
        });

        let mut catalog = OkxConnection::connect(OkxConnectionConfig {
            environment: "public".into(),
            rest_base_url: format!("http://{address}"),
            shared_quota: None,
        })
        .unwrap()
        .instrument_catalog(InstrumentType::Swap);
        let facts = catalog.fetch_instruments().await.unwrap();
        assert_eq!(facts.instruments[0].source_symbol, "BTC-USDT-SWAP");
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn public_market_snapshot_uses_the_callers_runtime() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = vec![0_u8; 4096];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            assert!(request.starts_with("GET /api/v5/market/ticker?instId=BTC-USDT-SWAP "));
            let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","bidPx":"60000.1","bidSz":"2","askPx":"60000.2","askSz":"3"}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).await.unwrap();
        });

        let mut snapshot = OkxConnection::connect(OkxConnectionConfig {
            environment: "public".into(),
            rest_base_url: format!("http://{address}"),
            shared_quota: None,
        })
        .unwrap()
        .market_snapshot(InstrumentType::Swap);
        let symbols = [ProviderSymbol::new("BTC-USDT-SWAP").unwrap()];
        let events = snapshot.fetch_snapshot(&symbols).await.unwrap();
        assert_eq!(events[0].symbol, "BTC-USDT-SWAP");
        assert_eq!(events[0].kind, MarketEventKind::Quote);
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn private_account_events_use_the_callers_runtime_and_answer_ping() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let _login = socket.next().await.unwrap().unwrap();
            socket
                .send(Message::Text(r#"{"event":"login","code":"0"}"#.into()))
                .await
                .unwrap();
            let _subscribe = socket.next().await.unwrap().unwrap();
            socket.send(Message::Ping(vec![4, 2].into())).await.unwrap();
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Pong(payload) if payload == vec![4, 2]
            ));
            socket
                .send(Message::Text(
                    r#"{"arg":{"channel":"account"},"data":[{"ccy":"USDT","eq":"10.5"}]}"#.into(),
                ))
                .await
                .unwrap();
        });
        let mut events = principal()
            .trading_account_events(
                InstrumentType::Spot,
                "spot",
                &OkxPrivateChannelConfig {
                    websocket_url: format!("ws://{address}"),
                    event_queue_capacity: 8,
                },
            )
            .unwrap();
        events.connect_channel().await.unwrap();
        let event = events.next_account_event().await.unwrap();
        let crate::application::capabilities::account_facts::ExternalAccountEvent::Snapshot(
            snapshot,
        ) = event
        else {
            panic!("expected snapshot")
        };
        assert_eq!(snapshot.balances[0].total.mantissa, 105);
        events.disconnect_channel().await.unwrap();
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn private_order_events_use_the_callers_runtime_and_preserve_route_binding() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let _login = socket.next().await.unwrap().unwrap();
            socket
                .send(Message::Text(r#"{"event":"login","code":"0"}"#.into()))
                .await
                .unwrap();
            let subscribe = socket.next().await.unwrap().unwrap();
            assert!(subscribe.to_string().contains(r#""channel":"orders""#));
            socket.send(Message::Ping(vec![8, 1].into())).await.unwrap();
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Pong(payload) if payload == vec![8, 1]
            ));
            socket
                .send(Message::Text(
                    r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","state":"live","sz":"1","accFillSz":"0","uTime":"1700000000000"}]}"#.into(),
                ))
                .await
                .unwrap();
        });
        let mut events = principal()
            .trading_order_events(
                InstrumentType::Swap,
                TradingMode::Cross,
                &OkxPrivateChannelConfig {
                    websocket_url: format!("ws://{address}"),
                    event_queue_capacity: 8,
                },
            )
            .unwrap();
        events.connect_channel().await.unwrap();
        let event = events.next_order_event().await.unwrap();
        assert_eq!(event.binding_id, "account.okx.main.trading.swap.cross");
        assert_eq!(event.channel_epoch, 1);
        assert_eq!(event.payload.order_id, "local-1");
        events.disconnect_channel().await.unwrap();
        server.await.unwrap();
    }
}
