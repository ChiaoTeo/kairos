use kairos_conflux::ConfluxSystem;
use kairos_integration::{
    CommandOutcome, ConnectionDescriptor, ExternalOrder, ExternalOrderQuery, IntegrationError,
    OrderCommand, OrderEntryEvent, OrderEntryRequest, OrderQuery, ParticipantInstrumentTypeRef,
};
use kairos_primitives::{AccountId, SegmentKey};

use super::{AsyncQueuedOrderEntry, AsyncQueuedOrderQuery, ExecutionWriterFence};
use crate::services::routing::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};

#[derive(Clone)]
pub(crate) struct ExecutionConnectionPlan {
    pub(crate) route_id: String,
    pub(crate) required: bool,
    pub(crate) account_id: AccountId,
    pub(crate) segment_key: SegmentKey,
    pub(crate) instrument_type: ParticipantInstrumentTypeRef,
    pub(crate) entry_key: String,
    pub(crate) query_key: String,
    pub(crate) stream_key: String,
    pub(crate) entry_descriptor: ConnectionDescriptor,
    pub(crate) query_descriptor: ConnectionDescriptor,
}

enum ManagedOrderEntry {
    BinanceSpot(kairos_integration::participants::binance::spot::BinanceSpotRestConnection),
    BinanceMargin(kairos_integration::participants::binance::margin::BinanceMarginRestConnection),
    BinanceUsdM(kairos_integration::participants::binance::usdm::BinanceUsdMRestConnection),
    BinanceCoinM(kairos_integration::participants::binance::coinm::BinanceCoinMRestConnection),
    BinanceOptions(
        kairos_integration::participants::binance::options::BinanceOptionsRestConnection,
    ),
    BinanceStocks(
        kairos_integration::participants::binance::advanced::stocks::BinanceStocksRestConnection,
    ),
    Okx(kairos_integration::participants::okx::private::OkxPrivateRestConnection),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderConnection),
}

enum ManagedOrderQuery {
    BinanceSpot(kairos_integration::participants::binance::spot::BinanceSpotRestConnection),
    BinanceMargin(kairos_integration::participants::binance::margin::BinanceMarginRestConnection),
    BinanceUsdM(kairos_integration::participants::binance::usdm::BinanceUsdMRestConnection),
    BinanceCoinM(kairos_integration::participants::binance::coinm::BinanceCoinMRestConnection),
    BinanceOptions(
        kairos_integration::participants::binance::options::BinanceOptionsRestConnection,
    ),
    BinanceStocks(
        kairos_integration::participants::binance::advanced::stocks::BinanceStocksRestConnection,
    ),
    Okx(kairos_integration::participants::okx::private::OkxPrivateRestConnection),
    Ibkr(kairos_integration::participants::ibkr::IbkrOrderConnection),
}

macro_rules! delegate_entry {
    ($self:expr, $method:ident, $($arg:expr),*) => {
        match $self {
            ManagedOrderEntry::BinanceSpot(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::BinanceMargin(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::BinanceUsdM(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::BinanceCoinM(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::BinanceOptions(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::BinanceStocks(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::Okx(value) => OrderCommand::$method(value, $($arg),*).await,
            ManagedOrderEntry::Ibkr(value) => OrderCommand::$method(value, $($arg),*).await,
        }
    };
}

impl OrderCommand for ManagedOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        delegate_entry!(self, submit_order, request)
    }
    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        delegate_entry!(self, cancel_order, request, remote_order_id, at_unix_nanos)
    }
}

macro_rules! delegate_query {
    ($self:expr, $method:ident, $query:expr) => {
        match $self {
            ManagedOrderQuery::BinanceSpot(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::BinanceMargin(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::BinanceUsdM(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::BinanceCoinM(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::BinanceOptions(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::BinanceStocks(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::Okx(value) => OrderQuery::$method(value, $query).await,
            ManagedOrderQuery::Ibkr(value) => OrderQuery::$method(value, $query).await,
        }
    };
}

impl OrderQuery for ManagedOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        delegate_query!(self, open_orders, query)
    }
    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        delegate_query!(self, order_history, query)
    }
    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        delegate_query!(self, order_detail, query)
    }
}

struct FencedOrderEntry {
    inner: RoutedAsyncOrderEntry<ManagedOrderEntry>,
    fences: Vec<ExecutionWriterFence>,
}

impl OrderCommand for FencedOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.validate(request)?;
        self.inner.submit_order(request).await
    }
    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.validate(request)?;
        self.inner
            .cancel_order(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl FencedOrderEntry {
    fn validate(&self, request: &OrderEntryRequest) -> Result<(), IntegrationError> {
        if self.fences.is_empty() {
            return Ok(());
        }
        self.fences
            .iter()
            .find(|fence| fence.validates(request))
            .ok_or_else(|| {
                IntegrationError::Authorization(format!(
                    "no Execution writer fence for account={}, segment={}",
                    request.account_id, request.segment_key
                ))
            })?
            .validate()
    }
}

pub(crate) fn build_managed_gateways(
    system: &mut ConfluxSystem,
    plans: &[ExecutionConnectionPlan],
    fences: Vec<ExecutionWriterFence>,
    shutdown: tokio::sync::watch::Receiver<bool>,
) -> Result<
    (
        AsyncQueuedOrderEntry,
        AsyncQueuedOrderQuery,
        impl std::future::Future<Output = ()> + Send + 'static,
        impl std::future::Future<Output = ()> + Send + 'static,
    ),
    String,
> {
    let mut entries = Vec::new();
    let mut queries = Vec::new();
    for plan in plans {
        let entry = take_entry(system, &plan.entry_key).ok_or_else(|| {
            format!(
                "missing managed Execution order connection: {}",
                plan.entry_key
            )
        })?;
        let query = take_query(system, &plan.query_key).ok_or_else(|| {
            format!(
                "missing managed Execution query connection: {}",
                plan.query_key
            )
        })?;
        entries.push(ExecutionRoute::new(
            plan.route_id.clone(),
            plan.account_id.clone(),
            plan.segment_key.clone(),
            Some(plan.instrument_type.clone()),
            plan.entry_descriptor.clone(),
            entry,
        )?);
        queries.push(ExecutionRoute::new(
            plan.route_id.clone(),
            plan.account_id.clone(),
            plan.segment_key.clone(),
            Some(plan.instrument_type.clone()),
            plan.query_descriptor.clone(),
            query,
        )?);
    }
    let entry = FencedOrderEntry {
        inner: RoutedAsyncOrderEntry::new(entries)?,
        fences,
    };
    let query = RoutedAsyncOrderQuery::new(queries)?;
    let (entry_proxy, entry_worker) = AsyncQueuedOrderEntry::channel(entry, 256);
    let (query_proxy, query_worker) = AsyncQueuedOrderQuery::channel(query, 256);
    let query_shutdown = shutdown.clone();
    Ok((
        entry_proxy,
        query_proxy,
        entry_worker.run(shutdown),
        query_worker.run(query_shutdown),
    ))
}

fn take_entry(system: &mut ConfluxSystem, key: &str) -> Option<ManagedOrderEntry> {
    let key = key.to_owned();
    macro_rules! take {
        ($field:ident, $variant:ident) => {
            if let Some(value) = system.$field.remove(&key) {
                return Some(ManagedOrderEntry::$variant(value.into_connection()));
            }
        };
    }
    take!(binance_spot_rest_connections, BinanceSpot);
    take!(binance_margin_rest_connections, BinanceMargin);
    take!(binance_usdm_rest_connections, BinanceUsdM);
    take!(binance_coinm_rest_connections, BinanceCoinM);
    take!(binance_options_rest_connections, BinanceOptions);
    take!(binance_stocks_rest_connections, BinanceStocks);
    take!(okx_private_rest_connections, Okx);
    take!(ibkr_order_connections, Ibkr);
    None
}

fn take_query(system: &mut ConfluxSystem, key: &str) -> Option<ManagedOrderQuery> {
    let key = key.to_owned();
    macro_rules! take {
        ($field:ident, $variant:ident) => {
            if let Some(value) = system.$field.remove(&key) {
                return Some(ManagedOrderQuery::$variant(value.into_connection()));
            }
        };
    }
    take!(binance_spot_rest_connections, BinanceSpot);
    take!(binance_margin_rest_connections, BinanceMargin);
    take!(binance_usdm_rest_connections, BinanceUsdM);
    take!(binance_coinm_rest_connections, BinanceCoinM);
    take!(binance_options_rest_connections, BinanceOptions);
    take!(binance_stocks_rest_connections, BinanceStocks);
    take!(okx_private_rest_connections, Okx);
    take!(ibkr_order_connections, Ibkr);
    None
}
