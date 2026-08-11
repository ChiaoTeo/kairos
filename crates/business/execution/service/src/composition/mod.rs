use std::path::PathBuf;

use crate::application::{
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink, ExecutionEvent, ExecutionSnapshot,
};
use crate::domain::RouteProduct;
use crate::services::persistence::ExecutionStateStore;
use crate::services::routing::{
    ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery, RoutedOrderEntry,
    RoutedOrderQuery,
};
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    IntegrationError,
};
use kairos_integration::application::{
    ConnectionDescriptor, ConnectionHealth, DecimalValue, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use kairos_integration::participants::binance::ConnectionDomain as BinanceConnectionDomain;
use kairos_integration::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, BinancePrincipalConnection,
    BinancePrincipalOrderQuotaAllocation, BinanceQuotaAllocation, BinanceSharedQuotaConfig,
    BinanceSpotChannelConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig, OkxPrincipalConfig,
    OkxPrincipalOrderQuotaAllocation, OkxPrincipalQuotaAllocation, OkxPrivateChannelConfig,
    OkxSharedQuotaConfig, TradingMode as OkxTradingMode,
};
use kairos_integration::participants::{binance, ibkr};
use secrecy::{ExposeSecret, SecretString};

pub use crate::services::sqlx_persistence::SqlxExecutionStore;

pub use crate::services::simulator::{
    ExecutionSimulator, SimulationConfig, SimulationFill, SimulationOrder, SimulationOrderRequest,
    SimulationOrderStatus, SimulationResult,
};

pub struct SharedExecutionSnapshotPublisher {
    inner: kairos_execution_contract::encoding::SharedExecutionSnapshotPublisher,
}

impl crate::application::ExecutionSnapshotPublisher for SharedExecutionSnapshotPublisher {
    fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        SharedExecutionSnapshotPublisher::publish(self, snapshot)
    }
}

impl SharedExecutionSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_execution_contract::encoding::SharedExecutionSnapshotPublisher::create(
                path, slot_size, actor_id,
            )?,
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_execution_contract::model::ExecutionSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

pub struct SharedIntentSnapshotPublisher {
    inner: kairos_execution_contract::encoding::SharedIntentSnapshotPublisher,
}

impl crate::application::IntentSnapshotPublisher for SharedIntentSnapshotPublisher {
    fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        SharedIntentSnapshotPublisher::publish(self, snapshot)
    }
}

impl SharedIntentSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_execution_contract::encoding::SharedIntentSnapshotPublisher::create(
                path, slot_size, actor_id,
            )?,
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_execution_contract::model::ExecutionSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

#[derive(Default)]
pub struct SimulatedOrderEntry;

#[derive(Clone, Debug)]
pub struct ExecutionConnectionOptions {
    /// Business route identity. It is never sent to Integration or a provider.
    pub route_id: String,
    /// Business account and segment served by this route.
    pub account_id: String,
    pub segment_key: String,
    pub provider: String,
    pub product: String,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
    pub base_url: String,
    pub websocket_url: String,
    pub request_weight_per_minute: u32,
    pub cancel_reserve_weight: u32,
    pub order_event_queue_capacity: usize,
    pub shared_quota_ledger_path: Option<PathBuf>,
    pub egress_scope_id: String,
    pub principal_scope_id: String,
    pub orders_per_10_seconds: u32,
    pub orders_per_day: u32,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

/// Business-owned capability composition for one configured execution route.
/// Integration defines each connection/capability; Execution decides which
/// capabilities belong to the route.
pub struct ExecutionConnections {
    pub descriptor: Option<ConnectionDescriptor>,
    pub descriptors: Vec<ConnectionDescriptor>,
    pub order_entry: Box<dyn OrderEntryConnection>,
    pub order_query: Option<Box<dyn OrderQueryConnection>>,
    pub execution_stream: Option<Box<dyn OrderEventSource>>,
    pub async_order_entry: Option<ExecutionAsyncOrderEntryRoutes>,
    pub async_order_query: Option<ExecutionAsyncOrderQueryRoutes>,
    pub async_execution_streams: Vec<ExecutionAsyncEventSource>,
}

/// Business-owned heterogeneous collection of concrete Integration order
/// entry capabilities. This enum only selects providers; it does not redefine
/// or narrow the Integration contract.
pub enum ExecutionAsyncOrderEntry {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderEntry),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEntry),
}

enum ExecutionBlockingOrderEntry {
    BinanceSpot(kairos_integration::blocking::BinanceSpotOrderEntry),
    OkxTrading(kairos_integration::blocking::OkxTradingOrderEntry),
}

impl OrderEntryConnection for ExecutionBlockingOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => connection.submit_order(request),
            Self::OkxTrading(connection) => connection.submit_order(request),
        }
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                connection.cancel_order(request, remote_order_id, at_unix_nanos)
            }
            Self::OkxTrading(connection) => {
                connection.cancel_order(request, remote_order_id, at_unix_nanos)
            }
        }
    }
}

/// Execution-owned route collection over concrete Integration capabilities.
/// The wrapper is public only so the binary can transfer it into the process;
/// its route table and provider values remain private.
pub struct ExecutionAsyncOrderEntryRoutes {
    inner: RoutedAsyncOrderEntry<ExecutionAsyncOrderEntry>,
}

impl AsyncOrderEntryConnection for ExecutionAsyncOrderEntryRoutes {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.inner.submit_order(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.inner
            .cancel_order(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderEntryConnection for ExecutionAsyncOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
        }
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
        }
    }
}

/// Business-owned provider selection for Integration's async query
/// capability. Provider-specific details remain on the concrete connection.
pub enum ExecutionAsyncOrderQuery {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderQuery),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderQuery),
}

enum ExecutionBlockingOrderQuery {
    BinanceSpot(kairos_integration::blocking::BinanceSpotOrderQuery),
    OkxTrading(kairos_integration::blocking::OkxTradingOrderQuery),
}

impl OrderQueryConnection for ExecutionBlockingOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => connection.open_orders(query),
            Self::OkxTrading(connection) => connection.open_orders(query),
        }
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => connection.order_history(query),
            Self::OkxTrading(connection) => connection.order_history(query),
        }
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => connection.order_detail(query),
            Self::OkxTrading(connection) => connection.order_detail(query),
        }
    }
}

pub struct ExecutionAsyncOrderQueryRoutes {
    inner: RoutedAsyncOrderQuery<ExecutionAsyncOrderQuery>,
}

impl AsyncOrderQueryConnection for ExecutionAsyncOrderQueryRoutes {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.order_history(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        self.inner.order_detail(query).await
    }
}

impl AsyncOrderQueryConnection for ExecutionAsyncOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
        }
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
        }
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::OkxTrading(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
        }
    }
}

/// Business-owned heterogeneous source collection. Integration keeps the
/// provider implementations concrete; Execution owns which sources form an
/// execution route.
pub enum ExecutionAsyncEventSource {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderEvents),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEvents),
}

impl AsyncOrderEventSource for ExecutionAsyncEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.connect_channel().await,
            Self::OkxTrading(source) => source.connect_channel().await,
        }
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.disconnect_channel().await,
            Self::OkxTrading(source) => source.disconnect_channel().await,
        }
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.reconnect_channel().await,
            Self::OkxTrading(source) => source.reconnect_channel().await,
        }
    }

    fn channel_health(&self) -> ConnectionHealth {
        match self {
            Self::BinanceSpot(source) => source.channel_health(),
            Self::OkxTrading(source) => source.channel_health(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.next_order_event().await,
            Self::OkxTrading(source) => source.next_order_event().await,
        }
    }
}

fn binance_spot_provider_connection(
    options: &ExecutionConnectionOptions,
) -> Result<BinanceConnection, String> {
    BinanceConnection::connect(BinanceConnectionConfig {
        environment: if options.base_url.to_ascii_lowercase().contains("testnet") {
            "testnet".into()
        } else {
            "live".into()
        },
        rest_base_url: options.base_url.clone(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: options.request_weight_per_minute,
            cancel_reserve_weight: options.cancel_reserve_weight,
        },
        shared_quota: options.shared_quota_ledger_path.clone().map(|ledger_path| {
            BinanceSharedQuotaConfig {
                ledger_path,
                egress_scope_id: options.egress_scope_id.clone(),
            }
        }),
    })
    .map_err(|error| error.to_string())
}

fn binance_spot_private_connection_from_provider(
    provider: &BinanceConnection,
    options: &ExecutionConnectionOptions,
) -> Result<BinancePrincipalConnection, String> {
    provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: format!("binance.principal.{}", options.principal_scope_id),
            principal_id: Some(options.principal_scope_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            principal_quota: options.shared_quota_ledger_path.as_ref().map(|_| {
                BinancePrincipalOrderQuotaAllocation {
                    orders_per_10_seconds: options.orders_per_10_seconds,
                    orders_per_day: options.orders_per_day,
                }
            }),
        })
        .map_err(|error| error.to_string())
}

fn binance_spot_channel_config(options: &ExecutionConnectionOptions) -> BinanceSpotChannelConfig {
    BinanceSpotChannelConfig {
        websocket_api_url: options.websocket_url.clone(),
        event_queue_capacity: options.order_event_queue_capacity,
    }
}

fn binance_spot_private_connection(
    options: &ExecutionConnectionOptions,
) -> Result<BinancePrincipalConnection, String> {
    let provider = binance_spot_provider_connection(options)?;
    binance_spot_private_connection_from_provider(&provider, options)
}

fn okx_trading_shape(
    product: &str,
) -> Result<(OkxInstrumentType, OkxTradingMode, RouteProduct), String> {
    match product.trim().to_ascii_lowercase().as_str() {
        "spot" => Ok((
            OkxInstrumentType::Spot,
            OkxTradingMode::Cash,
            RouteProduct::Spot,
        )),
        "cross-margin" | "margin" => Ok((
            OkxInstrumentType::Margin,
            OkxTradingMode::Cross,
            RouteProduct::CrossMargin,
        )),
        "isolated-margin" => Ok((
            OkxInstrumentType::Margin,
            OkxTradingMode::Isolated,
            RouteProduct::IsolatedMargin,
        )),
        "swap" | "usd-m-futures" => Ok((
            OkxInstrumentType::Swap,
            OkxTradingMode::Cross,
            RouteProduct::UsdMFutures,
        )),
        "futures" | "coin-m-futures" => Ok((
            OkxInstrumentType::Futures,
            OkxTradingMode::Cross,
            RouteProduct::CoinMFutures,
        )),
        "options" => Ok((
            OkxInstrumentType::Option,
            OkxTradingMode::Cross,
            RouteProduct::Options,
        )),
        other => Err(format!("unsupported OKX execution product: {other}")),
    }
}

fn okx_provider_connection(options: &ExecutionConnectionOptions) -> Result<OkxConnection, String> {
    OkxConnection::connect(OkxConnectionConfig {
        environment: if options.base_url.to_ascii_lowercase().contains("demo")
            || options.base_url.to_ascii_lowercase().contains("test")
        {
            "demo".into()
        } else {
            "live".into()
        },
        rest_base_url: options.base_url.clone(),
        shared_quota: options.shared_quota_ledger_path.clone().map(|ledger_path| {
            OkxSharedQuotaConfig {
                ledger_path,
                egress_scope_id: options.egress_scope_id.clone(),
            }
        }),
    })
    .map_err(|error| error.to_string())
}

fn okx_private_connection_from_provider(
    provider: &OkxConnection,
    options: &ExecutionConnectionOptions,
) -> Result<kairos_integration::participants::okx::OkxPrincipalConnection, String> {
    let quota_enabled = options.shared_quota_ledger_path.is_some();
    provider
        .principal_connection(OkxPrincipalConfig {
            binding_id: format!("okx.principal.{}", options.principal_scope_id),
            principal_id: Some(options.principal_scope_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            passphrase: options.passphrase.clone(),
            quota: quota_enabled.then_some(OkxPrincipalQuotaAllocation::default()),
            // Provider-owned conservative defaults remain overrideable on
            // OkxPrincipalConfig; cancels retain explicit emergency capacity.
            order_quota: quota_enabled.then_some(OkxPrincipalOrderQuotaAllocation::default()),
        })
        .map_err(|error| error.to_string())
}

fn okx_private_connection(
    options: &ExecutionConnectionOptions,
) -> Result<kairos_integration::participants::okx::OkxPrincipalConnection, String> {
    let provider = okx_provider_connection(options)?;
    okx_private_connection_from_provider(&provider, options)
}

fn same_binance_spot_provider_context(
    left: &ExecutionConnectionOptions,
    right: &ExecutionConnectionOptions,
) -> bool {
    left.base_url == right.base_url
        && left.request_weight_per_minute == right.request_weight_per_minute
        && left.cancel_reserve_weight == right.cancel_reserve_weight
        && left.shared_quota_ledger_path == right.shared_quota_ledger_path
        && left.egress_scope_id == right.egress_scope_id
}

fn same_okx_provider_context(
    left: &ExecutionConnectionOptions,
    right: &ExecutionConnectionOptions,
) -> bool {
    left.base_url == right.base_url
        && left.shared_quota_ledger_path == right.shared_quota_ledger_path
        && left.egress_scope_id == right.egress_scope_id
}

/// Compose all configured Execution routes in one business process. This is
/// intentionally an Execution collection, not an Integration registry.
/// Routes are heterogeneous concrete Integration handles. Binance Spot or OKX
/// Trading routes with the same endpoint/egress configuration share their
/// respective provider context while each principal projects independent
/// private capabilities and channels.
pub fn compose_execution_routes(
    options: &[ExecutionConnectionOptions],
) -> Result<ExecutionConnections, String> {
    let Some(_) = options.first() else {
        return Err("at least one Execution route is required".into());
    };
    if options.len() == 1 {
        return compose_execution_connections(&options[0]);
    }
    for option in options {
        let provider = option.provider.trim().to_ascii_lowercase();
        let product = option.product.trim().to_ascii_lowercase();
        match provider.as_str() {
            "binance" if product == "spot" => {}
            "okx" | "okex" => {
                okx_trading_shape(&product)?;
            }
            _ => {
                return Err(format!(
                    "multi-route async composition is not yet available for {} {}; migrate the provider-native capability first",
                    option.provider, option.product
                ))
            }
        }
    }

    let mut binance_contexts: Vec<(usize, BinanceConnection)> = Vec::new();
    let mut okx_contexts: Vec<(usize, OkxConnection)> = Vec::new();
    let mut descriptors = Vec::with_capacity(options.len());
    let mut blocking_entry_routes = Vec::with_capacity(options.len());
    let mut blocking_query_routes = Vec::with_capacity(options.len());
    let mut async_entry_routes = Vec::with_capacity(options.len());
    let mut async_query_routes = Vec::with_capacity(options.len());
    let mut streams = Vec::with_capacity(options.len());

    for (option_index, option) in options.iter().enumerate() {
        let account_id = kairos_domain_types::AccountId::new(option.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_domain_types::SegmentKey::new(option.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let provider = option.provider.trim().to_ascii_lowercase();
        let product = option.product.trim().to_ascii_lowercase();
        if provider == "binance" {
            let context_index = if let Some(index) =
                binance_contexts.iter().position(|(representative, _)| {
                    same_binance_spot_provider_context(&options[*representative], option)
                }) {
                index
            } else {
                binance_contexts.push((option_index, binance_spot_provider_connection(option)?));
                binance_contexts.len() - 1
            };
            let connection = binance_spot_private_connection_from_provider(
                &binance_contexts[context_index].1,
                option,
            )?;
            let descriptor = connection.spot_descriptor();
            let channel = binance_spot_channel_config(option);
            blocking_entry_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(RouteProduct::Spot),
                descriptor.clone(),
                ExecutionBlockingOrderEntry::BinanceSpot(
                    connection
                        .blocking_spot_order_entry()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            blocking_query_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(RouteProduct::Spot),
                descriptor.clone(),
                ExecutionBlockingOrderQuery::BinanceSpot(
                    connection
                        .blocking_spot_order_query()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            async_entry_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(RouteProduct::Spot),
                descriptor.clone(),
                ExecutionAsyncOrderEntry::BinanceSpot(
                    connection
                        .spot_order_entry()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            async_query_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id,
                segment_key,
                Some(RouteProduct::Spot),
                descriptor.clone(),
                ExecutionAsyncOrderQuery::BinanceSpot(
                    connection
                        .spot_order_query()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            streams.push(ExecutionAsyncEventSource::BinanceSpot(
                connection
                    .spot_order_events(&channel)
                    .map_err(|error| error.to_string())?,
            ));
            descriptors.push(descriptor);
            continue;
        }

        let (instrument_type, trading_mode, product_family) = okx_trading_shape(&product)?;
        let context_index = if let Some(index) =
            okx_contexts.iter().position(|(representative, _)| {
                same_okx_provider_context(&options[*representative], option)
            }) {
            index
        } else {
            okx_contexts.push((option_index, okx_provider_connection(option)?));
            okx_contexts.len() - 1
        };
        let connection =
            okx_private_connection_from_provider(&okx_contexts[context_index].1, option)?;
        let async_entry = connection
            .trading_order_entry(instrument_type, trading_mode)
            .map_err(|error| error.to_string())?;
        let entry_descriptor = async_entry.descriptor().clone();
        let async_query = connection.trading_order_query(instrument_type);
        let query_descriptor = async_query.descriptor().clone();
        blocking_entry_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(product_family),
            entry_descriptor.clone(),
            ExecutionBlockingOrderEntry::OkxTrading(
                connection
                    .blocking_trading_order_entry(instrument_type, trading_mode)
                    .map_err(|error| error.to_string())?,
            ),
        )?);
        blocking_query_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(product_family),
            query_descriptor.clone(),
            ExecutionBlockingOrderQuery::OkxTrading(
                connection.blocking_trading_order_query(instrument_type),
            ),
        )?);
        async_entry_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(product_family),
            entry_descriptor.clone(),
            ExecutionAsyncOrderEntry::OkxTrading(async_entry),
        )?);
        async_query_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id,
            segment_key,
            Some(product_family),
            query_descriptor,
            ExecutionAsyncOrderQuery::OkxTrading(async_query),
        )?);
        streams.push(ExecutionAsyncEventSource::OkxTrading(
            connection
                .trading_order_events(
                    instrument_type,
                    trading_mode,
                    &OkxPrivateChannelConfig {
                        websocket_url: option.websocket_url.clone(),
                        event_queue_capacity: option.order_event_queue_capacity,
                    },
                )
                .map_err(|error| error.to_string())?,
        ));
        descriptors.push(entry_descriptor);
    }

    Ok(ExecutionConnections {
        descriptor: descriptors.first().cloned(),
        descriptors,
        order_entry: Box::new(RoutedOrderEntry::new(blocking_entry_routes)?),
        order_query: Some(Box::new(RoutedOrderQuery::new(blocking_query_routes)?)),
        execution_stream: None,
        async_order_entry: Some(ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(async_entry_routes)?,
        }),
        async_order_query: Some(ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(async_query_routes)?,
        }),
        async_execution_streams: streams,
    })
}

pub fn compose_execution_connections(
    options: &ExecutionConnectionOptions,
) -> Result<ExecutionConnections, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product == "spot" {
        let connection = binance_spot_private_connection(options)?;
        let descriptor = connection.spot_descriptor();
        let channel = binance_spot_channel_config(options);
        let async_order_entry = ExecutionAsyncOrderEntry::BinanceSpot(
            connection
                .spot_order_entry()
                .map_err(|error| error.to_string())?,
        );
        let async_order_query = ExecutionAsyncOrderQuery::BinanceSpot(
            connection
                .spot_order_query()
                .map_err(|error| error.to_string())?,
        );
        let account_id = kairos_domain_types::AccountId::new(options.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_domain_types::SegmentKey::new(options.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let entry_routes = ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(RouteProduct::Spot),
                descriptor.clone(),
                async_order_entry,
            )?])?,
        };
        let query_routes = ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id,
                segment_key,
                Some(RouteProduct::Spot),
                descriptor.clone(),
                async_order_query,
            )?])?,
        };
        return Ok(ExecutionConnections {
            descriptor: Some(descriptor.clone()),
            descriptors: vec![descriptor],
            order_entry: Box::new(
                connection
                    .blocking_spot_order_entry()
                    .map_err(|error| error.to_string())?,
            ),
            order_query: Some(Box::new(
                connection
                    .blocking_spot_order_query()
                    .map_err(|error| error.to_string())?,
            )),
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncEventSource::BinanceSpot(
                connection
                    .spot_order_events(&channel)
                    .map_err(|error| error.to_string())?,
            )],
        });
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, trading_mode, product_family) = okx_trading_shape(&product)?;
        let connection = okx_private_connection(options)?;
        let async_entry = connection
            .trading_order_entry(instrument_type, trading_mode)
            .map_err(|error| error.to_string())?;
        let entry_descriptor = async_entry.descriptor().clone();
        let async_query = connection.trading_order_query(instrument_type);
        let query_descriptor = async_query.descriptor().clone();
        let order_events = connection
            .trading_order_events(
                instrument_type,
                trading_mode,
                &OkxPrivateChannelConfig {
                    websocket_url: options.websocket_url.clone(),
                    event_queue_capacity: options.order_event_queue_capacity,
                },
            )
            .map_err(|error| error.to_string())?;
        let account_id = kairos_domain_types::AccountId::new(options.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_domain_types::SegmentKey::new(options.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let entry_routes = ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(product_family),
                entry_descriptor.clone(),
                ExecutionAsyncOrderEntry::OkxTrading(async_entry),
            )?])?,
        };
        let query_routes = ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id,
                segment_key,
                Some(product_family),
                query_descriptor,
                ExecutionAsyncOrderQuery::OkxTrading(async_query),
            )?])?,
        };
        return Ok(ExecutionConnections {
            descriptor: Some(entry_descriptor.clone()),
            descriptors: vec![entry_descriptor],
            order_entry: Box::new(ExecutionBlockingOrderEntry::OkxTrading(
                connection
                    .blocking_trading_order_entry(instrument_type, trading_mode)
                    .map_err(|error| error.to_string())?,
            )),
            order_query: Some(Box::new(ExecutionBlockingOrderQuery::OkxTrading(
                connection.blocking_trading_order_query(instrument_type),
            ))),
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncEventSource::OkxTrading(order_events)],
        });
    }

    Ok(ExecutionConnections {
        descriptor: None,
        descriptors: Vec::new(),
        order_entry: compose_order_entry(options)?,
        order_query: compose_order_query(options)?,
        execution_stream: compose_execution_stream(options)?,
        async_order_entry: None,
        async_order_query: None,
        async_execution_streams: Vec::new(),
    })
}

pub fn compose_order_entry(
    options: &ExecutionConnectionOptions,
) -> Result<Box<dyn OrderEntryConnection>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    if provider == "simulated" {
        return Ok(Box::new(SimulatedOrderEntry::default()));
    }
    let product_name = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product_name == "spot" {
        return Ok(Box::new(
            binance_spot_private_connection(options)?
                .blocking_spot_order_entry()
                .map_err(|error| error.to_string())?,
        ));
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, trading_mode, _) = okx_trading_shape(&product_name)?;
        return Ok(Box::new(
            okx_private_connection(options)?
                .blocking_trading_order_entry(instrument_type, trading_mode)
                .map_err(|error| error.to_string())?,
        ));
    }
    match provider.as_str() {
        "ibkr" => ibkr::blocking::order_entry(&ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        })
        .map_err(|error| error.to_string()),
        "binance" => match product_name.as_str() {
            "equity" | "stocks" => binance::blocking::equity_order_entry(
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            "cross-margin" | "margin" => binance::blocking::margin_order_entry(
                BinanceConnectionDomain::CrossMargin,
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            "isolated-margin" => binance::blocking::margin_order_entry(
                BinanceConnectionDomain::IsolatedMargin,
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            "options" => binance::blocking::options_order_entry(
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            "usd-m-futures" | "swap" => binance::blocking::futures_order_entry(
                BinanceConnectionDomain::UsdMFutures,
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            "coin-m-futures" | "futures" => binance::blocking::futures_order_entry(
                BinanceConnectionDomain::CoinMFutures,
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map_err(|error| error.to_string()),
            _ => {
                return Err(format!(
                    "unsupported Binance execution product: {product_name}"
                ))
            }
        },
        _ => Err(format!("unsupported execution provider: {provider}")),
    }
}

pub fn compose_order_query(
    options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderQueryConnection>>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product == "spot" {
        return Ok(Some(Box::new(
            binance_spot_private_connection(options)?
                .blocking_spot_order_query()
                .map_err(|error| error.to_string())?,
        )));
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, _, _) = okx_trading_shape(&product)?;
        return Ok(Some(Box::new(
            okx_private_connection(options)?.blocking_trading_order_query(instrument_type),
        )));
    }
    let product_family = match product.as_str() {
        "equity" | "stocks" => {
            return binance::blocking::equity_order_query(
                options.api_key.expose_secret().to_owned(),
                options.secret.expose_secret().to_owned(),
                options.base_url.clone(),
            )
            .map(Some)
            .map_err(|error| error.to_string())
        }
        "spot" => BinanceConnectionDomain::Spot,
        "usd-m-futures" | "swap" => BinanceConnectionDomain::UsdMFutures,
        "coin-m-futures" | "futures" => BinanceConnectionDomain::CoinMFutures,
        "options" => BinanceConnectionDomain::Options,
        _ => return Ok(None),
    };
    if provider == "binance" {
        binance::blocking::order_query(
            product_family,
            options.api_key.expose_secret().to_owned(),
            options.secret.expose_secret().to_owned(),
            options.base_url.clone(),
        )
        .map(Some)
        .map_err(|error| error.to_string())
    } else {
        Ok(None)
    }
}

pub fn compose_execution_stream(
    options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderEventSource>>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    if provider != "ibkr" {
        return Ok(None);
    }
    let connection = ibkr::blocking::execution_stream(
        &ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        },
        "",
        None,
    )
    .map_err(|error| error.to_string())?;
    Ok(Some(connection))
}

impl OrderEntryConnection for SimulatedOrderEntry {
    fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            remote_order_id: kairos_domain_types::RemoteOrderId::new(format!(
                "simulated:{}",
                request.order_id
            ))
            .ok(),
            filled_quantity: Some(DecimalValue::new(0, request.quantity.scale)),
            occurred_at_unix_nanos: now_nanos().into(),
            reason: String::new(),
        }))
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        Ok(CommandOutcome::Confirmed(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            remote_order_id: kairos_domain_types::RemoteOrderId::new(remote_order_id).ok(),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos.into(),
            reason: String::new(),
        }))
    }
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

pub struct MemoryStateStore(pub Option<ExecutionSnapshot>);
impl ExecutionStateStore for MemoryStateStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        Ok(self.0.clone())
    }
    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.0 = Some(snapshot.clone());
        Ok(())
    }
}

pub struct MemoryExecutionAudit(pub Vec<ExecutionEvent>);
impl MemoryExecutionAudit {
    pub fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.0.push(event.clone());
        Ok(())
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        Ok(self
            .0
            .iter()
            .enumerate()
            .map(|(index, event)| {
                Ok::<_, String>(ExecutionAuditEvent {
                    sequence: (index as u64 + 1).into(),
                    order_id: event.order_id.clone(),
                    status: event.status,
                    remote_order_id: event.remote_order_id.clone(),
                    occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                    reason: event.reason.clone(),
                })
            })
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .filter(|event| {
                query
                    .order_id
                    .as_deref()
                    .is_none_or(|value| event.order_id.as_str() == value)
                    && query.status.as_deref().is_none_or(|value| {
                        format!("{:?}", event.status).eq_ignore_ascii_case(value)
                    })
            })
            .take(query.limit.unwrap_or(u32::MAX) as usize)
            .collect())
    }
}

impl ExecutionAuditSink for MemoryExecutionAudit {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        Self::publish(self, event)
    }

    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String> {
        Self::query(self, query)
    }
}

pub struct FileExecutionStore {
    path: PathBuf,
}

impl FileExecutionStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl ExecutionStateStore for FileExecutionStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        match std::fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map(Some)
                .map_err(|error| error.to_string()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.path.with_extension("tmp");
        let bytes = serde_json::to_vec_pretty(snapshot).map_err(|error| error.to_string())?;
        std::fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
        std::fs::rename(&temporary, &self.path).map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod secret_tests {
    use super::{
        compose_execution_connections, compose_execution_routes, ExecutionConnectionOptions,
    };

    fn binance_spot_options() -> ExecutionConnectionOptions {
        ExecutionConnectionOptions {
            route_id: "binance.spot".into(),
            account_id: "main".into(),
            segment_key: "spot".into(),
            provider: "binance".into(),
            product: "spot".into(),
            api_key: "api-key-secret".into(),
            secret: "api-secret".into(),
            passphrase: "passphrase-secret".into(),
            base_url: "https://testnet.binance.vision".into(),
            websocket_url: "wss://ws-api.testnet.binance.vision/ws-api/v3".into(),
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
            order_event_queue_capacity: 1_024,
            shared_quota_ledger_path: None,
            egress_scope_id: "test-egress".into(),
            principal_scope_id: "test-principal".into(),
            orders_per_10_seconds: 50,
            orders_per_day: 160_000,
            host: "127.0.0.1".into(),
            port: 4002,
            client_id: 0,
        }
    }

    #[test]
    fn execution_connection_debug_redacts_credentials() {
        let options = binance_spot_options();
        let output = format!("{options:?}");
        assert!(!output.contains("api-key-secret"));
        assert!(!output.contains("api-secret"));
        assert!(!output.contains("passphrase-secret"));
    }

    #[test]
    fn binance_spot_route_uses_one_native_provider_context() {
        let connections = compose_execution_connections(&binance_spot_options()).unwrap();
        let descriptor = connections.descriptor.expect("native route descriptor");

        assert_eq!(descriptor.binding_id, "binance.principal.test-principal");
        assert_eq!(descriptor.environment, "testnet");
        assert!(connections.order_query.is_some());
        assert!(connections.execution_stream.is_none());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn okx_route_projects_native_async_and_blocking_traits() {
        let mut options = binance_spot_options();
        options.route_id = "okx.swap".into();
        options.provider = "okx".into();
        options.product = "swap".into();
        options.segment_key = "swap".into();
        options.base_url = "https://www.okx.com".into();
        let connections = compose_execution_connections(&options).unwrap();
        let descriptor = connections.descriptor.expect("native route descriptor");

        assert_eq!(
            descriptor.binding_id,
            "okx.principal.test-principal.trading.swap.cross"
        );
        assert_eq!(descriptor.domain.as_str(), "trading");
        assert_eq!(descriptor.participant.id.as_str(), "okx");
        assert!(connections.order_query.is_some());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn one_execution_process_composes_binance_and_okx_routes() {
        let binance = binance_spot_options();
        let mut okx = binance_spot_options();
        okx.route_id = "okx.swap".into();
        okx.provider = "okx".into();
        okx.product = "swap".into();
        okx.segment_key = "swap".into();
        okx.base_url = "https://www.okx.com".into();
        okx.websocket_url = "wss://ws.okx.com:8443/ws/v5/private".into();
        okx.principal_scope_id = "okx-principal".into();

        let connections = compose_execution_routes(&[binance, okx]).unwrap();
        assert_eq!(connections.descriptors.len(), 2);
        assert_eq!(connections.async_execution_streams.len(), 2);
        assert!(connections.descriptors.iter().any(|descriptor| descriptor
            .participant
            .id
            .as_str()
            == "binance"));
        assert!(connections.descriptors.iter().any(|descriptor| descriptor
            .participant
            .id
            .as_str()
            == "okx"));
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
    }
}

mod preflight;

pub use preflight::{QueuedExecutionPreflight, SocketExecutionPreflight};
