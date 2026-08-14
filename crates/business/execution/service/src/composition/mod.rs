use std::path::PathBuf;

use crate::application::{
    ExecutionAsyncRoute, ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink,
    ExecutionEvent, ExecutionSnapshot,
};
use crate::domain::RouteProduct;
use crate::services::gateway::{
    AsyncQueuedOrderEntry, AsyncQueuedOrderEventSource, AsyncQueuedOrderQuery,
};
use crate::services::persistence::ExecutionStateStore;
use crate::services::routing::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
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
    BinanceConnection, BinanceConnectionConfig, BinanceFuturesChannelConfig,
    BinanceMarginChannelConfig, BinanceOptionsChannelConfig, BinancePrincipalConfig,
    BinancePrincipalConnection, BinancePrincipalOrderQuotaAllocation, BinanceQuotaAllocation,
    BinanceSharedQuotaConfig, BinanceSpotChannelConfig,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig, OkxPrincipalConfig,
    OkxPrincipalOrderQuotaAllocation, OkxPrincipalQuotaAllocation, OkxPrivateChannelConfig,
    OkxSharedQuotaConfig, TradingMode as OkxTradingMode,
};
use kairos_integration::participants::{binance, ibkr};
use secrecy::{ExposeSecret, SecretString};

mod v2_publishers;

pub use v2_publishers::{
    AeronExecutionEventPublisher, SharedExecutionSnapshotPublisher, SharedIntentSnapshotPublisher,
};

pub use crate::services::sqlx_persistence::SqlxExecutionStore;

pub use crate::services::simulator::{
    ExecutionSimulator, SimulationConfig, SimulationFill, SimulationOrder, SimulationOrderRequest,
    SimulationOrderStatus, SimulationResult,
};

#[derive(Default)]
pub struct SimulatedOrderEntry;

#[derive(Clone, Debug)]
pub struct ExecutionConnectionOptions {
    /// Business route identity. It is never sent to Integration or a provider.
    pub route_id: String,
    /// Required routes gate process readiness. Optional routes may start and
    /// recover independently while the process reports degraded.
    pub required: bool,
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
    /// Provider symbol required by Binance isolated-margin listen-key scope.
    pub isolated_symbol: Option<String>,
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
    /// Explicit synchronous projection for CLI/offline callers and providers
    /// whose native async slice has not migrated yet. Production live routes
    /// with async capabilities leave this empty; the process installs its
    /// bounded Actor proxy before accepting requests.
    pub order_entry: Option<Box<dyn OrderEntryConnection>>,
    pub order_query: Option<Box<dyn OrderQueryConnection>>,
    pub execution_stream: Option<Box<dyn OrderEventSource>>,
    pub async_order_entry: Option<ExecutionAsyncOrderEntryRoutes>,
    pub async_order_query: Option<ExecutionAsyncOrderQueryRoutes>,
    pub async_execution_streams: Vec<ExecutionAsyncRoute<ExecutionAsyncEventSource>>,
}

pub struct DirectExecutionConnections {
    order_entry: Option<Box<dyn OrderEntryConnection>>,
    order_query: Option<Box<dyn OrderQueryConnection>>,
    execution_stream: Option<Box<dyn OrderEventSource>>,
    runtime: DirectExecutionRuntime,
}

impl DirectExecutionConnections {
    pub fn into_parts(
        self,
    ) -> (
        Option<Box<dyn OrderEntryConnection>>,
        Option<Box<dyn OrderQueryConnection>>,
        Option<Box<dyn OrderEventSource>>,
        DirectExecutionRuntime,
    ) {
        (
            self.order_entry,
            self.order_query,
            self.execution_stream,
            self.runtime,
        )
    }
}

pub struct DirectExecutionRuntime {
    shutdown: Option<tokio::sync::watch::Sender<bool>>,
    _tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl DirectExecutionRuntime {
    fn none() -> Self {
        Self {
            shutdown: None,
            _tasks: Vec::new(),
        }
    }
}

impl Drop for DirectExecutionRuntime {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(true);
        }
    }
}

/// Business-owned heterogeneous collection of concrete Integration order
/// entry capabilities. This enum only selects providers; it does not redefine
/// or narrow the Integration contract.
pub enum ExecutionAsyncOrderEntry {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotOrderEntry),
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderEntry),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderEntry),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderEntry),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderEntry),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEntry),
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
            Self::BinanceFutures(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderEntryConnection::submit_order(connection, request).await
            }
            Self::Ibkr(connection) => {
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
            Self::BinanceFutures(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderEntryConnection::cancel_order(
                    connection,
                    request,
                    remote_order_id,
                    at_unix_nanos,
                )
                .await
            }
            Self::Ibkr(connection) => {
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
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderQuery),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderQuery),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderQuery),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderQuery),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderQuery),
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
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::open_orders(connection, query).await
            }
            Self::Ibkr(connection) => {
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
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::order_history(connection, query).await
            }
            Self::Ibkr(connection) => {
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
            Self::BinanceFutures(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::BinanceMargin(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::BinanceOptions(connection) => {
                AsyncOrderQueryConnection::order_detail(connection, query).await
            }
            Self::Ibkr(connection) => {
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
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesOrderEvents),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginOrderEvents),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsOrderEvents),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderEvents),
    OkxTrading(kairos_integration::participants::okx::OkxTradingOrderEvents),
}

impl AsyncOrderEventSource for ExecutionAsyncEventSource {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.connect_channel().await,
            Self::BinanceFutures(source) => source.connect_channel().await,
            Self::BinanceMargin(source) => source.connect_channel().await,
            Self::BinanceOptions(source) => source.connect_channel().await,
            Self::Ibkr(source) => source.connect_channel().await,
            Self::OkxTrading(source) => source.connect_channel().await,
        }
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.disconnect_channel().await,
            Self::BinanceFutures(source) => source.disconnect_channel().await,
            Self::BinanceMargin(source) => source.disconnect_channel().await,
            Self::BinanceOptions(source) => source.disconnect_channel().await,
            Self::Ibkr(source) => source.disconnect_channel().await,
            Self::OkxTrading(source) => source.disconnect_channel().await,
        }
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.reconnect_channel().await,
            Self::BinanceFutures(source) => source.reconnect_channel().await,
            Self::BinanceMargin(source) => source.reconnect_channel().await,
            Self::BinanceOptions(source) => source.reconnect_channel().await,
            Self::Ibkr(source) => source.reconnect_channel().await,
            Self::OkxTrading(source) => source.reconnect_channel().await,
        }
    }

    fn channel_health(&self) -> ConnectionHealth {
        match self {
            Self::BinanceSpot(source) => source.channel_health(),
            Self::BinanceFutures(source) => source.channel_health(),
            Self::BinanceMargin(source) => source.channel_health(),
            Self::BinanceOptions(source) => source.channel_health(),
            Self::Ibkr(source) => source.channel_health(),
            Self::OkxTrading(source) => source.channel_health(),
        }
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        match self {
            Self::BinanceSpot(source) => source.next_order_event().await,
            Self::BinanceFutures(source) => source.next_order_event().await,
            Self::BinanceMargin(source) => source.next_order_event().await,
            Self::BinanceOptions(source) => source.next_order_event().await,
            Self::Ibkr(source) => source.next_order_event().await,
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
        let provider = options[0].provider.trim().to_ascii_lowercase();
        let product = options[0].product.trim().to_ascii_lowercase();
        if matches!(provider.as_str(), "simulated" | "paper") {
            return compose_execution_connections(&options[0]);
        }
        if provider == "ibkr" {
            return compose_ibkr_async_execution(&options[0]);
        }
        let native_async = (provider == "binance"
            && matches!(
                product.as_str(),
                "spot"
                    | "cross-margin"
                    | "margin"
                    | "isolated-margin"
                    | "usd-m-futures"
                    | "swap"
                    | "coin-m-futures"
                    | "futures"
                    | "options"
            ))
            || provider == "okx"
            || provider == "okex";
        if !native_async {
            return Err(format!(
                "production async execution route is not available for {} {}; migrate the provider-native async capability",
                options[0].provider, options[0].product
            ));
        }
    }
    let mut ibkr_client_identities = std::collections::BTreeSet::new();
    for option in options {
        let provider = option.provider.trim().to_ascii_lowercase();
        let product = option.product.trim().to_ascii_lowercase();
        match provider.as_str() {
            "binance"
                if matches!(
                    product.as_str(),
                    "spot"
                        | "cross-margin"
                        | "margin"
                        | "isolated-margin"
                        | "usd-m-futures"
                        | "swap"
                        | "coin-m-futures"
                        | "futures"
                        | "options"
                ) => {}
            "ibkr" if matches!(product.as_str(), "equity" | "stocks") => {
                let identity = (
                    option.host.trim().to_ascii_lowercase(),
                    option.port,
                    option.client_id,
                );
                if !ibkr_client_identities.insert(identity) {
                    return Err(format!(
                        "duplicate IBKR host/port/client_id across Execution routes: {}:{} client_id={}; allocate a distinct TWS client id per order-event route",
                        option.host, option.port, option.client_id
                    ));
                }
            }
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
            if matches!(
                product.as_str(),
                "cross-margin" | "margin" | "isolated-margin"
            ) {
                let (margin, route_product, isolated_symbol) = if product == "isolated-margin" {
                    let symbol = option
                        .isolated_symbol
                        .as_ref()
                        .filter(|value| !value.trim().is_empty())
                        .ok_or_else(|| {
                            "Binance isolated-margin route requires isolated_symbol".to_string()
                        })?
                        .to_ascii_uppercase();
                    (
                        connection
                            .isolated_margin_connection(symbol.clone())
                            .map_err(|error| error.to_string())?,
                        RouteProduct::IsolatedMargin,
                        Some(symbol),
                    )
                } else {
                    (
                        connection.cross_margin_connection(),
                        RouteProduct::CrossMargin,
                        None,
                    )
                };
                let descriptor = margin.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(route_product),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceMargin(
                        margin.order_entry().map_err(|error| error.to_string())?,
                    ),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(route_product),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceMargin(
                        margin.order_query().map_err(|error| error.to_string())?,
                    ),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceMargin(
                            margin
                                .order_events(&BinanceMarginChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    isolated_symbol,
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            if matches!(
                product.as_str(),
                "usd-m-futures" | "swap" | "coin-m-futures" | "futures"
            ) {
                let (futures, route_product) =
                    if matches!(product.as_str(), "usd-m-futures" | "swap") {
                        (
                            connection
                                .usd_m_futures_connection()
                                .map_err(|error| error.to_string())?,
                            RouteProduct::UsdMFutures,
                        )
                    } else {
                        (
                            connection
                                .coin_m_futures_connection()
                                .map_err(|error| error.to_string())?,
                            RouteProduct::CoinMFutures,
                        )
                    };
                let descriptor = futures.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(route_product),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceFutures(futures.order_entry()),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(route_product),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceFutures(futures.order_query()),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceFutures(
                            futures
                                .order_events(&BinanceFuturesChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            if product == "options" {
                let options_connection = connection
                    .options_connection()
                    .map_err(|error| error.to_string())?;
                let descriptor = options_connection.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(RouteProduct::Options),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceOptions(options_connection.order_entry()),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(RouteProduct::Options),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceOptions(options_connection.order_query()),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceOptions(
                            options_connection
                                .order_events(&BinanceOptionsChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            let descriptor = connection.spot_descriptor();
            let channel = binance_spot_channel_config(option);
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
            streams.push(
                ExecutionAsyncRoute::new(
                    option.route_id.clone(),
                    option.required,
                    ExecutionAsyncEventSource::BinanceSpot(
                        connection
                            .spot_order_events(&channel)
                            .map_err(|error| error.to_string())?,
                    ),
                )
                .with_binding_id(descriptor.binding_id.clone()),
            );
            descriptors.push(descriptor);
            continue;
        }

        if provider == "ibkr" {
            let connection = ibkr::IbkrConnection::connect(
                ibkr::IbkrConnectionConfig {
                    host: option.host.clone(),
                    port: option.port,
                    client_id: option.client_id,
                },
                format!("ibkr.principal.{}", option.principal_scope_id),
                option.account_id.clone(),
            )
            .map_err(|error| error.to_string())?;
            let descriptor = connection.descriptor().clone();
            async_entry_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(RouteProduct::Equity),
                descriptor.clone(),
                ExecutionAsyncOrderEntry::Ibkr(connection.order_entry()),
            )?);
            async_query_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id,
                segment_key,
                Some(RouteProduct::Equity),
                descriptor.clone(),
                ExecutionAsyncOrderQuery::Ibkr(connection.order_query()),
            )?);
            streams.push(
                ExecutionAsyncRoute::new(
                    option.route_id.clone(),
                    option.required,
                    ExecutionAsyncEventSource::Ibkr(connection.order_events(None)),
                )
                .with_binding_id(descriptor.binding_id.clone()),
            );
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
        streams.push(
            ExecutionAsyncRoute::new(
                option.route_id.clone(),
                option.required,
                ExecutionAsyncEventSource::OkxTrading(
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
                ),
            )
            .with_binding_id(entry_descriptor.binding_id.clone()),
        );
        descriptors.push(entry_descriptor);
    }

    Ok(ExecutionConnections {
        descriptor: descriptors.first().cloned(),
        descriptors,
        order_entry: None,
        order_query: None,
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

fn compose_ibkr_async_execution(
    options: &ExecutionConnectionOptions,
) -> Result<ExecutionConnections, String> {
    match options.product.trim().to_ascii_lowercase().as_str() {
        "equity" | "stocks" => {}
        product => return Err(format!("unsupported IBKR execution product: {product}")),
    }
    let connection = ibkr::IbkrConnection::connect(
        ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        },
        format!("ibkr.principal.{}", options.principal_scope_id),
        options.account_id.clone(),
    )
    .map_err(|error| error.to_string())?;
    let descriptor = connection.descriptor().clone();
    let account_id = kairos_domain_types::AccountId::new(options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let segment_key = kairos_domain_types::SegmentKey::new(options.segment_key.clone())
        .map_err(|error| error.to_string())?;
    let entry_routes = ExecutionAsyncOrderEntryRoutes {
        inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
            options.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(RouteProduct::Equity),
            descriptor.clone(),
            ExecutionAsyncOrderEntry::Ibkr(connection.order_entry()),
        )?])?,
    };
    let query_routes = ExecutionAsyncOrderQueryRoutes {
        inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
            options.route_id.clone(),
            account_id,
            segment_key,
            Some(RouteProduct::Equity),
            descriptor.clone(),
            ExecutionAsyncOrderQuery::Ibkr(connection.order_query()),
        )?])?,
    };
    let binding_id = descriptor.binding_id.clone();
    Ok(ExecutionConnections {
        descriptor: Some(descriptor.clone()),
        descriptors: vec![descriptor],
        order_entry: None,
        order_query: None,
        execution_stream: None,
        async_order_entry: Some(entry_routes),
        async_order_query: Some(query_routes),
        async_execution_streams: vec![ExecutionAsyncRoute::new(
            options.route_id.clone(),
            options.required,
            ExecutionAsyncEventSource::Ibkr(connection.order_events(None)),
        )
        .with_binding_id(binding_id)],
    })
}

/// Compose one-shot/direct CLI adapters. Migrated providers use their native
/// async capability and bounded Execution proxies; remaining explicit CLI
/// Direct one-shot adapters are retained only for explicitly synchronous
/// callers; the production route is composed exclusively from async clients.
pub fn compose_direct_execution_connections(
    options: &ExecutionConnectionOptions,
) -> Result<DirectExecutionConnections, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    let native_async_direct = provider == "ibkr"
        || (provider == "binance"
            && matches!(
                product.as_str(),
                "cross-margin"
                    | "margin"
                    | "isolated-margin"
                    | "usd-m-futures"
                    | "swap"
                    | "coin-m-futures"
                    | "futures"
                    | "options"
            ));
    if !native_async_direct {
        let connections = compose_execution_connections(options)?;
        return Ok(DirectExecutionConnections {
            order_entry: connections.order_entry,
            order_query: connections.order_query,
            execution_stream: connections.execution_stream,
            runtime: DirectExecutionRuntime::none(),
        });
    }
    tokio::runtime::Handle::try_current()
        .map_err(|_| "direct async execution requires a caller-owned Tokio runtime".to_string())?;
    let mut connections = if provider == "ibkr" {
        compose_ibkr_async_execution(options)?
    } else {
        compose_execution_routes(std::slice::from_ref(options))?
    };
    let entry = connections
        .async_order_entry
        .take()
        .ok_or_else(|| "async order-entry capability is missing".to_string())?;
    let query = connections
        .async_order_query
        .take()
        .ok_or_else(|| "async order-query capability is missing".to_string())?;
    let source = connections
        .async_execution_streams
        .pop()
        .ok_or_else(|| "async order-event capability is missing".to_string())?
        .into_source();
    let (entry_proxy, entry_worker) = AsyncQueuedOrderEntry::channel(entry, 16);
    let (query_proxy, query_worker) = AsyncQueuedOrderQuery::channel(query, 16);
    let (event_proxy, event_worker) = AsyncQueuedOrderEventSource::channel(source, 4);
    let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
    let tasks = vec![
        tokio::spawn(entry_worker.run(shutdown_rx.clone())),
        tokio::spawn(query_worker.run(shutdown_rx.clone())),
        tokio::spawn(event_worker.run(shutdown_rx)),
    ];
    Ok(DirectExecutionConnections {
        order_entry: Some(Box::new(entry_proxy)),
        order_query: Some(Box::new(query_proxy)),
        execution_stream: Some(Box::new(event_proxy)),
        runtime: DirectExecutionRuntime {
            shutdown: Some(shutdown),
            _tasks: tasks,
        },
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
        let binding_id = descriptor.binding_id.clone();
        return Ok(ExecutionConnections {
            descriptor: Some(descriptor.clone()),
            descriptors: vec![descriptor],
            order_entry: None,
            order_query: None,
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncRoute::new(
                options.route_id.clone(),
                options.required,
                ExecutionAsyncEventSource::BinanceSpot(
                    connection
                        .spot_order_events(&channel)
                        .map_err(|error| error.to_string())?,
                ),
            )
            .with_binding_id(binding_id)],
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
        let binding_id = entry_descriptor.binding_id.clone();
        return Ok(ExecutionConnections {
            descriptor: Some(entry_descriptor.clone()),
            descriptors: vec![entry_descriptor],
            order_entry: None,
            order_query: None,
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncRoute::new(
                options.route_id.clone(),
                options.required,
                ExecutionAsyncEventSource::OkxTrading(order_events),
            )
            .with_binding_id(binding_id)],
        });
    }

    Ok(ExecutionConnections {
        descriptor: None,
        descriptors: Vec::new(),
        order_entry: Some(compose_order_entry(options)?),
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
    if matches!(provider.as_str(), "simulated" | "paper") {
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
        "binance" => match product_name.as_str() {
            "equity" | "stocks" => Err(
                "Binance does not publish a supported Equity/Stocks execution API; use a broker route such as IBKR"
                    .into(),
            ),
            "cross-margin" | "margin" => Err(
                "Binance Cross Margin is async-only; use production/direct async composition"
                    .into(),
            ),
            "isolated-margin" => Err(
                "Binance Isolated Margin is async-only; use production/direct async composition"
                    .into(),
            ),
            "options" => {
                Err("Binance Options is async-only; use production/direct async composition".into())
            }
            "usd-m-futures" | "swap" => Err(
                "Binance USD-M Futures is async-only; use production/direct async composition"
                    .into(),
            ),
            "coin-m-futures" | "futures" => Err(
                "Binance COIN-M Futures is async-only; use production/direct async composition"
                    .into(),
            ),
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
        "equity" | "stocks" => return Ok(None),
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
    _options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderEventSource>>, String> {
    Ok(None)
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
        compose_direct_execution_connections, compose_execution_connections,
        compose_execution_routes, ExecutionConnectionOptions,
    };

    fn binance_spot_options() -> ExecutionConnectionOptions {
        ExecutionConnectionOptions {
            route_id: "binance.spot".into(),
            required: true,
            account_id: "main".into(),
            segment_key: "spot".into(),
            provider: "binance".into(),
            product: "spot".into(),
            api_key: "api-key-secret".into(),
            secret: "api-secret".into(),
            passphrase: "passphrase-secret".into(),
            base_url: "https://testnet.binance.vision".into(),
            websocket_url: "wss://ws-api.testnet.binance.vision/ws-api/v3".into(),
            isolated_symbol: None,
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
        assert!(connections.order_entry.is_some());
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
    fn ibkr_production_route_projects_only_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "ibkr.equity".into();
        options.provider = "ibkr".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();
        options.principal_scope_id = "tws-client-0".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("IBKR descriptor");

        assert_eq!(descriptor.binding_id, "ibkr.principal.tws-client-0");
        assert_eq!(descriptor.participant.id.as_str(), "ibkr");
        assert_eq!(descriptor.principal_id.as_deref(), Some("client-id:0"));
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.execution_stream.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_usd_m_futures_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.usdm".into();
        options.provider = "binance".into();
        options.product = "usd-m-futures".into();
        options.segment_key = "futures".into();
        options.base_url = "https://testnet.binancefuture.com".into();
        options.websocket_url = "wss://stream.binancefuture.com".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("futures descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.usd-m-futures"
        );
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.execution_stream.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_coin_m_futures_keeps_a_distinct_async_product_route() {
        let mut options = binance_spot_options();
        options.route_id = "binance.coinm".into();
        options.provider = "binance".into();
        options.product = "coin-m-futures".into();
        options.segment_key = "coin-m-futures".into();
        options.base_url = "https://testnet.binancefuture.com".into();
        options.websocket_url = "wss://dstream.binancefuture.com".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("COIN-M descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.coin-m-futures"
        );
        assert_eq!(descriptor.domain.as_str(), "coin-m-futures");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_cross_margin_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.cross-margin".into();
        options.product = "cross-margin".into();
        options.segment_key = "cross-margin".into();
        options.websocket_url = "wss://stream.binance.com:9443".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("margin descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.cross-margin"
        );
        assert_eq!(descriptor.domain.as_str(), "cross-margin");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_isolated_margin_requires_and_scopes_the_provider_symbol() {
        let mut options = binance_spot_options();
        options.route_id = "binance.isolated-margin.btcusdt".into();
        options.product = "isolated-margin".into();
        options.segment_key = "isolated-margin-btcusdt".into();

        let error = compose_execution_routes(&[options.clone()])
            .err()
            .expect("isolated route without symbol must fail");
        assert!(error.contains("isolated_symbol"));

        options.isolated_symbol = Some("btcusdt".into());
        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("isolated descriptor");

        assert_eq!(descriptor.domain.as_str(), "isolated-margin");
        assert!(connections.order_entry.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn production_rejects_unmigrated_live_blocking_provider_slice() {
        let mut options = binance_spot_options();
        options.route_id = "binance.equity".into();
        options.provider = "binance".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();

        let error = compose_execution_routes(&[options])
            .err()
            .expect("unmigrated live route must fail");

        assert!(error.contains("production async execution route is not available"));
        assert!(error.contains("equity"));
    }

    #[test]
    fn binance_options_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.options".into();
        options.product = "options".into();
        options.segment_key = "options".into();
        options.websocket_url = "wss://nbstream.binance.com/eoptions/private/stream".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("options descriptor");

        assert_eq!(descriptor.domain.as_str(), "options");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn multi_route_composes_distinct_ibkr_client_sessions() {
        let mut first = binance_spot_options();
        first.route_id = "ibkr-main".into();
        first.provider = "ibkr".into();
        first.product = "equity".into();
        first.segment_key = "equity-main".into();
        first.account_id = "DU111".into();
        first.principal_scope_id = "ibkr-client-11".into();
        first.client_id = 11;
        let mut second = first.clone();
        second.route_id = "ibkr-secondary".into();
        second.segment_key = "equity-secondary".into();
        second.account_id = "DU222".into();
        second.principal_scope_id = "ibkr-client-12".into();
        second.client_id = 12;

        let connections = compose_execution_routes(&[first, second]).unwrap();

        assert_eq!(connections.descriptors.len(), 2);
        assert_eq!(connections.async_execution_streams.len(), 2);
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
    }

    #[test]
    fn multi_route_rejects_duplicate_ibkr_client_identity() {
        let mut first = binance_spot_options();
        first.route_id = "ibkr-main".into();
        first.provider = "ibkr".into();
        first.product = "equity".into();
        first.segment_key = "equity-main".into();
        first.account_id = "DU111".into();
        first.client_id = 11;
        let mut second = first.clone();
        second.route_id = "ibkr-secondary".into();
        second.segment_key = "equity-secondary".into();
        second.account_id = "DU222".into();

        let error = compose_execution_routes(&[first, second])
            .err()
            .expect("duplicate IBKR client identity must fail");

        assert!(error.contains("distinct TWS client id"));
    }

    #[tokio::test]
    async fn ibkr_direct_cli_uses_async_gateway_proxies() {
        let mut options = binance_spot_options();
        options.route_id = "ibkr-direct".into();
        options.provider = "ibkr".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();

        let direct = compose_direct_execution_connections(&options).unwrap();
        let (entry, query, events, runtime) = direct.into_parts();

        assert!(entry.is_some());
        assert!(query.is_some());
        assert!(events.is_some());
        drop(runtime);
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
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
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

    #[test]
    fn production_route_composition_does_not_construct_blocking_capabilities() {
        let source = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/composition/mod.rs"),
        )
        .unwrap();
        let production = source
            .split("pub fn compose_execution_routes")
            .nth(1)
            .and_then(|source| source.split("pub fn compose_execution_connections").next())
            .expect("production route composition source");

        assert!(!production.contains(".blocking_"));
        assert!(!production.contains("kairos_integration::blocking"));
        assert!(!production.contains("ExecutionBlocking"));
    }
}

mod preflight;

pub use preflight::{QueuedExecutionPreflight, SocketExecutionPreflight};
