use kairos_account_contract::view::AccountViewReader;
use kairos_account_contract::{
    AccountClient, AccountEventPublisher, AccountEventStream, AccountViewPublisher,
};
use kairos_execution_contract::{
    ExecutionClient, ExecutionEventPublisher, ExecutionEventStream, ExecutionViewPublisher,
    ExecutionViewReader,
};
use kairos_integration::participants::{
    binance::{
        advanced::stocks::{
            BinanceStocksRestConnection, BinanceStocksUserWebSocketConnection,
            BinanceStocksWebSocketConnection,
        },
        coinm::{
            BinanceCoinMRestConnection, BinanceCoinMUserWebSocketConnection,
            BinanceCoinMWebSocketConnection,
        },
        funding::BinanceFundingRestConnection,
        margin::{
            BinanceMarginRestConnection, BinanceMarginUserWebSocketConnection,
            BinanceMarginWebSocketConnection,
        },
        options::{
            BinanceOptionsRestConnection, BinanceOptionsUserWebSocketConnection,
            BinanceOptionsWebSocketConnection,
        },
        spot::{
            BinanceSpotRestConnection, BinanceSpotUserWebSocketConnection,
            BinanceSpotWebSocketConnection,
        },
        usdm::{
            BinanceUsdMRestConnection, BinanceUsdMUserWebSocketConnection,
            BinanceUsdMWebSocketConnection,
        },
        BinanceRestConfig, BinanceUserWebSocketConfig, BinanceWebSocketConfig,
    },
    hyperliquid::{
        info::HyperliquidInfoRestConnection, HyperliquidRestConfig, HyperliquidWebSocketConfig,
        HyperliquidWebSocketConnection,
    },
    ibkr::{
        IbkrAccountQueryConfig, IbkrAccountQueryConnection, IbkrAccountStreamConfig,
        IbkrAccountStreamConnection, IbkrExecutionStreamConfig, IbkrExecutionStreamConnection,
        IbkrMarketDataConfig, IbkrMarketDataConnection, IbkrOrderConfig, IbkrOrderConnection,
    },
    massive::{
        MassiveOptionsWebSocketConnection, MassiveRestConfig, MassiveRestConnection,
        MassiveStocksWebSocketConnection, MassiveWebSocketConfig,
    },
    okx::{
        private::{OkxPrivateRestConnection, OkxPrivateWebSocketConnection},
        public::{OkxPublicRestConnection, OkxPublicWebSocketConnection},
    },
};
use kairos_integration::ConnectionKey;
use kairos_market_contract::{
    MarketClient, MarketEventPublisher, MarketEventStream, MarketViewPublisher, MarketViewReader,
};
use kairos_reference_contract::{ReferenceClient, ReferenceEventPublisher, ReferenceEventStream};
use kairos_risk_contract::{
    MmapRiskSnapshotPublisher, RiskAeronEventPublisher, RiskClient, RiskEventStream, RiskViewReader,
};
use kairos_transport::{AeronBytePublisher, SharedSnapshotReader, SharedSnapshotWriter};
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::marker::PhantomData;
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use thiserror::Error;
use tokio::time::Instant;

use crate::{
    IntegrationEvent, ManagedClients, ManagedConnectionIdentity, ManagedConnections,
    NamedResources, ResourceState, SystemEvent,
};

pub(crate) enum ConnectionDriverOutput {
    Integration(IntegrationEvent),
    System(SystemEvent),
    Account {
        client: String,
        frame: kairos_account_contract::AccountEventFrame,
    },
    Execution {
        client: String,
        frame: kairos_execution_contract::ExecutionEventFrame,
    },
    Market {
        client: String,
        frame: kairos_market_contract::MarketEventFrame,
    },
    Reference {
        client: String,
        frame: kairos_reference_contract::ReferenceEventFrame,
    },
    Risk {
        client: String,
        frame: kairos_risk_contract::RiskEventFrame,
    },
}

pub(crate) struct ConnectionDriverState {
    family_cursor: usize,
    contract_family_cursor: usize,
    ready: VecDeque<(String, ConnectionDriverOutput)>,
    occupied: HashSet<String>,
    maintenance_in_progress: HashSet<String>,
}

struct SystemTimer {
    period: Duration,
    next: Instant,
}

impl ConnectionDriverState {
    pub(crate) fn new() -> Self {
        Self {
            family_cursor: 0,
            contract_family_cursor: 0,
            ready: VecDeque::new(),
            occupied: HashSet::new(),
            maintenance_in_progress: HashSet::new(),
        }
    }

    fn push(&mut self, source: String, output: ConnectionDriverOutput) {
        if self.occupied.insert(source.clone()) {
            self.ready.push_back((source, output));
        }
    }

    fn pop(&mut self) -> Option<ConnectionDriverOutput> {
        let (source, output) = self.ready.pop_front()?;
        self.occupied.remove(&source);
        Some(output)
    }
}

const CONNECTION_EVENT_FAMILIES: usize = 19;

#[derive(Debug, Error)]
pub enum ConnectionAccessError {
    #[error("connection `{0}` does not exist")]
    NotFound(ConnectionKey),
    #[error("connection `{0}` is retiring")]
    Retiring(ConnectionKey),
}

#[derive(Debug, Error)]
pub enum ConnectionCreateError {
    #[error("connection `{0}` already exists")]
    AlreadyExists(ConnectionKey),
    #[error(transparent)]
    Integration(#[from] kairos_integration::IntegrationError),
    #[error(transparent)]
    Resource(#[from] crate::ResourceError),
}

pub struct TypedConnectionCollection<'a, C, P> {
    connections: &'a mut ManagedConnections<String, C>,
    constructor: fn(ConnectionKey, P) -> Result<C, kairos_integration::IntegrationError>,
    parameters: PhantomData<fn(P)>,
}

impl<'a, C, P> TypedConnectionCollection<'a, C, P> {
    fn new(
        connections: &'a mut ManagedConnections<String, C>,
        constructor: fn(ConnectionKey, P) -> Result<C, kairos_integration::IntegrationError>,
    ) -> Self {
        Self {
            connections,
            constructor,
            parameters: PhantomData,
        }
    }

    pub fn create(
        &mut self,
        key: ConnectionKey,
        parameters: P,
    ) -> Result<(), ConnectionCreateError> {
        if self.connections.get(&key.to_string()).is_some() {
            return Err(ConnectionCreateError::AlreadyExists(key));
        }
        let connection = (self.constructor)(key.clone(), parameters)?;
        if !self.connections.insert_new(key.to_string(), connection)? {
            return Err(ConnectionCreateError::AlreadyExists(key));
        }
        Ok(())
    }

    pub fn get(&mut self, key: &ConnectionKey) -> Result<&mut C, ConnectionAccessError> {
        let managed = self
            .connections
            .get_mut(&key.to_string())
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))?;
        if managed.state() == ResourceState::Retiring {
            return Err(ConnectionAccessError::Retiring(key.clone()));
        }
        Ok(managed.connection_mut())
    }

    pub fn keys(&self) -> Vec<ConnectionKey> {
        self.connections
            .iter()
            .filter_map(|(key, _)| ConnectionKey::new(key.clone()).ok())
            .collect()
    }

    pub fn generation(&self, key: &ConnectionKey) -> Result<u64, ConnectionAccessError> {
        self.connections
            .get(&key.to_string())
            .map(|managed| managed.generation())
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))
    }

    pub fn remove(&mut self, key: &ConnectionKey) -> Result<(), ConnectionAccessError> {
        self.connections
            .remove(&key.to_string())
            .map(drop)
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))
    }
}

pub struct ConnectionCollections<'a> {
    pub binance_spot_rest:
        TypedConnectionCollection<'a, BinanceSpotRestConnection, BinanceRestConfig>,
    pub binance_funding_rest:
        TypedConnectionCollection<'a, BinanceFundingRestConnection, BinanceRestConfig>,
    pub binance_margin_rest:
        TypedConnectionCollection<'a, BinanceMarginRestConnection, BinanceRestConfig>,
    pub binance_usdm_rest:
        TypedConnectionCollection<'a, BinanceUsdMRestConnection, BinanceRestConfig>,
    pub binance_coinm_rest:
        TypedConnectionCollection<'a, BinanceCoinMRestConnection, BinanceRestConfig>,
    pub binance_options_rest:
        TypedConnectionCollection<'a, BinanceOptionsRestConnection, BinanceRestConfig>,
    pub binance_stocks_rest:
        TypedConnectionCollection<'a, BinanceStocksRestConnection, BinanceRestConfig>,
    pub binance_spot_websocket:
        TypedConnectionCollection<'a, BinanceSpotWebSocketConnection, BinanceWebSocketConfig>,
    pub binance_usdm_websocket:
        TypedConnectionCollection<'a, BinanceUsdMWebSocketConnection, BinanceWebSocketConfig>,
    pub binance_coinm_websocket:
        TypedConnectionCollection<'a, BinanceCoinMWebSocketConnection, BinanceWebSocketConfig>,
    pub binance_options_websocket:
        TypedConnectionCollection<'a, BinanceOptionsWebSocketConnection, BinanceWebSocketConfig>,
    pub binance_stocks_websocket:
        TypedConnectionCollection<'a, BinanceStocksWebSocketConnection, BinanceWebSocketConfig>,
    pub binance_spot_user_websocket: TypedConnectionCollection<
        'a,
        BinanceSpotUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub binance_margin_user_websocket: TypedConnectionCollection<
        'a,
        BinanceMarginUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub binance_usdm_user_websocket: TypedConnectionCollection<
        'a,
        BinanceUsdMUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub binance_coinm_user_websocket: TypedConnectionCollection<
        'a,
        BinanceCoinMUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub binance_options_user_websocket: TypedConnectionCollection<
        'a,
        BinanceOptionsUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub binance_stocks_user_websocket: TypedConnectionCollection<
        'a,
        BinanceStocksUserWebSocketConnection,
        BinanceUserWebSocketConfig,
    >,
    pub ibkr_account_query:
        TypedConnectionCollection<'a, IbkrAccountQueryConnection, IbkrAccountQueryConfig>,
    pub ibkr_account_stream:
        TypedConnectionCollection<'a, IbkrAccountStreamConnection, IbkrAccountStreamConfig>,
    pub ibkr_order: TypedConnectionCollection<'a, IbkrOrderConnection, IbkrOrderConfig>,
    pub ibkr_execution_stream:
        TypedConnectionCollection<'a, IbkrExecutionStreamConnection, IbkrExecutionStreamConfig>,
    pub ibkr_market_data:
        TypedConnectionCollection<'a, IbkrMarketDataConnection, IbkrMarketDataConfig>,
    pub hyperliquid_info_rest:
        TypedConnectionCollection<'a, HyperliquidInfoRestConnection, HyperliquidRestConfig>,
    pub massive_rest: TypedConnectionCollection<'a, MassiveRestConnection, MassiveRestConfig>,
    pub massive_stocks_websocket:
        TypedConnectionCollection<'a, MassiveStocksWebSocketConnection, MassiveWebSocketConfig>,
    pub massive_options_websocket:
        TypedConnectionCollection<'a, MassiveOptionsWebSocketConnection, MassiveWebSocketConfig>,
    pub hyperliquid_websocket:
        TypedConnectionCollection<'a, HyperliquidWebSocketConnection, HyperliquidWebSocketConfig>,
    pub okx_public_rest: TypedConnectionCollection<
        'a,
        OkxPublicRestConnection,
        kairos_integration::participants::okx::OkxRestConfig,
    >,
    pub okx_public_websocket: TypedConnectionCollection<
        'a,
        OkxPublicWebSocketConnection,
        kairos_integration::participants::okx::OkxWebSocketConfig,
    >,
    pub okx_private_rest: TypedConnectionCollection<
        'a,
        OkxPrivateRestConnection,
        kairos_integration::participants::okx::OkxPrivateRestConfig,
    >,
    pub okx_private_websocket: TypedConnectionCollection<
        'a,
        OkxPrivateWebSocketConnection,
        kairos_integration::participants::okx::OkxPrivateWebSocketConfig,
    >,
}

/// The one concrete resource universe available to every Kairos Actor.
pub struct ConfluxSystem {
    timers: BTreeMap<String, SystemTimer>,
    pending_system_events: VecDeque<(String, SystemEvent)>,

    pub account_clients: ManagedClients<String, AccountClient>,
    pub execution_clients: ManagedClients<String, ExecutionClient>,
    pub market_clients: ManagedClients<String, MarketClient>,
    pub reference_clients: ManagedClients<String, ReferenceClient>,
    pub risk_clients: ManagedClients<String, RiskClient>,

    pub account_event_streams: NamedResources<String, AccountEventStream>,
    pub account_event_publishers: NamedResources<String, AccountEventPublisher>,
    pub execution_event_streams: NamedResources<String, ExecutionEventStream>,
    pub execution_event_publishers: NamedResources<String, ExecutionEventPublisher>,
    pub market_event_streams: NamedResources<String, MarketEventStream>,
    pub market_event_publishers: NamedResources<String, MarketEventPublisher>,
    pub reference_event_streams: NamedResources<String, ReferenceEventStream>,
    pub reference_event_publishers: NamedResources<String, ReferenceEventPublisher>,
    pub risk_event_streams: NamedResources<String, RiskEventStream>,

    pub account_view_readers: NamedResources<String, AccountViewReader>,
    pub account_view_publishers: NamedResources<String, AccountViewPublisher>,
    pub execution_view_readers: NamedResources<String, ExecutionViewReader>,
    pub execution_view_publishers: NamedResources<String, ExecutionViewPublisher>,
    pub market_view_readers: NamedResources<String, MarketViewReader>,
    pub market_view_publishers: NamedResources<String, MarketViewPublisher>,
    pub risk_view_readers: NamedResources<String, RiskViewReader>,
    pub risk_snapshot_publishers: NamedResources<String, MmapRiskSnapshotPublisher>,
    pub risk_event_publishers: NamedResources<String, RiskAeronEventPublisher>,

    /// Process-owned transport resources. These collections manage concrete
    /// Aeron and mmap handles without pretending that their byte APIs are a
    /// business Contract. Contract codecs remain owned by each module.
    pub aeron_publishers: NamedResources<String, AeronBytePublisher>,
    pub mmap_readers: NamedResources<String, SharedSnapshotReader>,
    pub mmap_writers: NamedResources<String, SharedSnapshotWriter>,

    pub(crate) binance_spot_rest_connections: ManagedConnections<String, BinanceSpotRestConnection>,
    pub(crate) binance_spot_websocket_connections:
        ManagedConnections<String, BinanceSpotWebSocketConnection>,
    pub(crate) binance_spot_user_websocket_connections:
        ManagedConnections<String, BinanceSpotUserWebSocketConnection>,
    pub(crate) binance_funding_rest_connections:
        ManagedConnections<String, BinanceFundingRestConnection>,
    pub(crate) binance_margin_rest_connections:
        ManagedConnections<String, BinanceMarginRestConnection>,
    pub(crate) binance_margin_websocket_connections:
        ManagedConnections<String, BinanceMarginWebSocketConnection>,
    pub(crate) binance_margin_user_websocket_connections:
        ManagedConnections<String, BinanceMarginUserWebSocketConnection>,
    pub(crate) binance_usdm_rest_connections: ManagedConnections<String, BinanceUsdMRestConnection>,
    pub(crate) binance_usdm_websocket_connections:
        ManagedConnections<String, BinanceUsdMWebSocketConnection>,
    pub(crate) binance_usdm_user_websocket_connections:
        ManagedConnections<String, BinanceUsdMUserWebSocketConnection>,
    pub(crate) binance_coinm_rest_connections:
        ManagedConnections<String, BinanceCoinMRestConnection>,
    pub(crate) binance_coinm_websocket_connections:
        ManagedConnections<String, BinanceCoinMWebSocketConnection>,
    pub(crate) binance_coinm_user_websocket_connections:
        ManagedConnections<String, BinanceCoinMUserWebSocketConnection>,
    pub(crate) binance_options_rest_connections:
        ManagedConnections<String, BinanceOptionsRestConnection>,
    pub(crate) binance_options_websocket_connections:
        ManagedConnections<String, BinanceOptionsWebSocketConnection>,
    pub(crate) binance_options_user_websocket_connections:
        ManagedConnections<String, BinanceOptionsUserWebSocketConnection>,
    pub(crate) binance_stocks_rest_connections:
        ManagedConnections<String, BinanceStocksRestConnection>,
    pub(crate) binance_stocks_websocket_connections:
        ManagedConnections<String, BinanceStocksWebSocketConnection>,
    pub(crate) binance_stocks_user_websocket_connections:
        ManagedConnections<String, BinanceStocksUserWebSocketConnection>,

    pub(crate) okx_public_rest_connections: ManagedConnections<String, OkxPublicRestConnection>,
    pub(crate) okx_public_websocket_connections:
        ManagedConnections<String, OkxPublicWebSocketConnection>,
    pub(crate) okx_private_rest_connections: ManagedConnections<String, OkxPrivateRestConnection>,
    pub(crate) okx_private_websocket_connections:
        ManagedConnections<String, OkxPrivateWebSocketConnection>,

    pub(crate) hyperliquid_info_rest_connections:
        ManagedConnections<String, HyperliquidInfoRestConnection>,
    pub(crate) hyperliquid_websocket_connections:
        ManagedConnections<String, HyperliquidWebSocketConnection>,

    pub(crate) ibkr_account_query_connections:
        ManagedConnections<String, IbkrAccountQueryConnection>,
    pub(crate) ibkr_account_stream_connections:
        ManagedConnections<String, IbkrAccountStreamConnection>,
    pub(crate) ibkr_order_connections: ManagedConnections<String, IbkrOrderConnection>,
    pub(crate) ibkr_execution_stream_connections:
        ManagedConnections<String, IbkrExecutionStreamConnection>,
    pub(crate) ibkr_market_data_connections: ManagedConnections<String, IbkrMarketDataConnection>,
    pub(crate) massive_rest_connections: ManagedConnections<String, MassiveRestConnection>,
    pub(crate) massive_stocks_websocket_connections:
        ManagedConnections<String, MassiveStocksWebSocketConnection>,
    pub(crate) massive_options_websocket_connections:
        ManagedConnections<String, MassiveOptionsWebSocketConnection>,
}

impl ConfluxSystem {
    pub fn new() -> Self {
        Self {
            timers: BTreeMap::new(),
            pending_system_events: VecDeque::new(),
            account_clients: ManagedClients::new(),
            execution_clients: ManagedClients::new(),
            market_clients: ManagedClients::new(),
            reference_clients: ManagedClients::new(),
            risk_clients: ManagedClients::new(),
            account_event_streams: NamedResources::new(),
            account_event_publishers: NamedResources::new(),
            execution_event_streams: NamedResources::new(),
            execution_event_publishers: NamedResources::new(),
            market_event_streams: NamedResources::new(),
            market_event_publishers: NamedResources::new(),
            reference_event_streams: NamedResources::new(),
            reference_event_publishers: NamedResources::new(),
            risk_event_streams: NamedResources::new(),
            account_view_readers: NamedResources::new(),
            account_view_publishers: NamedResources::new(),
            execution_view_readers: NamedResources::new(),
            execution_view_publishers: NamedResources::new(),
            market_view_readers: NamedResources::new(),
            market_view_publishers: NamedResources::new(),
            risk_view_readers: NamedResources::new(),
            risk_snapshot_publishers: NamedResources::new(),
            risk_event_publishers: NamedResources::new(),
            aeron_publishers: NamedResources::new(),
            mmap_readers: NamedResources::new(),
            mmap_writers: NamedResources::new(),
            binance_spot_rest_connections: ManagedConnections::new(),
            binance_spot_websocket_connections: ManagedConnections::new(),
            binance_spot_user_websocket_connections: ManagedConnections::new(),
            binance_funding_rest_connections: ManagedConnections::new(),
            binance_margin_rest_connections: ManagedConnections::new(),
            binance_margin_websocket_connections: ManagedConnections::new(),
            binance_margin_user_websocket_connections: ManagedConnections::new(),
            binance_usdm_rest_connections: ManagedConnections::new(),
            binance_usdm_websocket_connections: ManagedConnections::new(),
            binance_usdm_user_websocket_connections: ManagedConnections::new(),
            binance_coinm_rest_connections: ManagedConnections::new(),
            binance_coinm_websocket_connections: ManagedConnections::new(),
            binance_coinm_user_websocket_connections: ManagedConnections::new(),
            binance_options_rest_connections: ManagedConnections::new(),
            binance_options_websocket_connections: ManagedConnections::new(),
            binance_options_user_websocket_connections: ManagedConnections::new(),
            binance_stocks_rest_connections: ManagedConnections::new(),
            binance_stocks_websocket_connections: ManagedConnections::new(),
            binance_stocks_user_websocket_connections: ManagedConnections::new(),
            okx_public_rest_connections: ManagedConnections::new(),
            okx_public_websocket_connections: ManagedConnections::new(),
            okx_private_rest_connections: ManagedConnections::new(),
            okx_private_websocket_connections: ManagedConnections::new(),
            hyperliquid_info_rest_connections: ManagedConnections::new(),
            hyperliquid_websocket_connections: ManagedConnections::new(),
            ibkr_account_query_connections: ManagedConnections::new(),
            ibkr_account_stream_connections: ManagedConnections::new(),
            ibkr_order_connections: ManagedConnections::new(),
            ibkr_execution_stream_connections: ManagedConnections::new(),
            ibkr_market_data_connections: ManagedConnections::new(),
            massive_rest_connections: ManagedConnections::new(),
            massive_stocks_websocket_connections: ManagedConnections::new(),
            massive_options_websocket_connections: ManagedConnections::new(),
        }
    }

    pub fn connections(&mut self) -> ConnectionCollections<'_> {
        ConnectionCollections {
            binance_spot_rest: TypedConnectionCollection::new(
                &mut self.binance_spot_rest_connections,
                BinanceSpotRestConnection::new,
            ),
            binance_funding_rest: TypedConnectionCollection::new(
                &mut self.binance_funding_rest_connections,
                BinanceFundingRestConnection::new,
            ),
            binance_margin_rest: TypedConnectionCollection::new(
                &mut self.binance_margin_rest_connections,
                BinanceMarginRestConnection::new,
            ),
            binance_usdm_rest: TypedConnectionCollection::new(
                &mut self.binance_usdm_rest_connections,
                BinanceUsdMRestConnection::new,
            ),
            binance_coinm_rest: TypedConnectionCollection::new(
                &mut self.binance_coinm_rest_connections,
                BinanceCoinMRestConnection::new,
            ),
            binance_options_rest: TypedConnectionCollection::new(
                &mut self.binance_options_rest_connections,
                BinanceOptionsRestConnection::new,
            ),
            binance_stocks_rest: TypedConnectionCollection::new(
                &mut self.binance_stocks_rest_connections,
                BinanceStocksRestConnection::new,
            ),
            binance_spot_websocket: TypedConnectionCollection::new(
                &mut self.binance_spot_websocket_connections,
                BinanceSpotWebSocketConnection::new,
            ),
            binance_usdm_websocket: TypedConnectionCollection::new(
                &mut self.binance_usdm_websocket_connections,
                BinanceUsdMWebSocketConnection::new,
            ),
            binance_coinm_websocket: TypedConnectionCollection::new(
                &mut self.binance_coinm_websocket_connections,
                BinanceCoinMWebSocketConnection::new,
            ),
            binance_options_websocket: TypedConnectionCollection::new(
                &mut self.binance_options_websocket_connections,
                BinanceOptionsWebSocketConnection::new,
            ),
            binance_stocks_websocket: TypedConnectionCollection::new(
                &mut self.binance_stocks_websocket_connections,
                BinanceStocksWebSocketConnection::new,
            ),
            binance_spot_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_spot_user_websocket_connections,
                BinanceSpotUserWebSocketConnection::new,
            ),
            binance_margin_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_margin_user_websocket_connections,
                BinanceMarginUserWebSocketConnection::new,
            ),
            binance_usdm_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_usdm_user_websocket_connections,
                BinanceUsdMUserWebSocketConnection::new,
            ),
            binance_coinm_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_coinm_user_websocket_connections,
                BinanceCoinMUserWebSocketConnection::new,
            ),
            binance_options_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_options_user_websocket_connections,
                BinanceOptionsUserWebSocketConnection::new,
            ),
            binance_stocks_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_stocks_user_websocket_connections,
                BinanceStocksUserWebSocketConnection::new,
            ),
            ibkr_account_query: TypedConnectionCollection::new(
                &mut self.ibkr_account_query_connections,
                IbkrAccountQueryConnection::new,
            ),
            ibkr_account_stream: TypedConnectionCollection::new(
                &mut self.ibkr_account_stream_connections,
                IbkrAccountStreamConnection::new,
            ),
            ibkr_order: TypedConnectionCollection::new(
                &mut self.ibkr_order_connections,
                IbkrOrderConnection::new,
            ),
            ibkr_execution_stream: TypedConnectionCollection::new(
                &mut self.ibkr_execution_stream_connections,
                IbkrExecutionStreamConnection::new,
            ),
            ibkr_market_data: TypedConnectionCollection::new(
                &mut self.ibkr_market_data_connections,
                IbkrMarketDataConnection::new,
            ),
            hyperliquid_info_rest: TypedConnectionCollection::new(
                &mut self.hyperliquid_info_rest_connections,
                HyperliquidInfoRestConnection::new,
            ),
            massive_rest: TypedConnectionCollection::new(
                &mut self.massive_rest_connections,
                MassiveRestConnection::new,
            ),
            massive_stocks_websocket: TypedConnectionCollection::new(
                &mut self.massive_stocks_websocket_connections,
                MassiveStocksWebSocketConnection::new,
            ),
            massive_options_websocket: TypedConnectionCollection::new(
                &mut self.massive_options_websocket_connections,
                MassiveOptionsWebSocketConnection::new,
            ),
            hyperliquid_websocket: TypedConnectionCollection::new(
                &mut self.hyperliquid_websocket_connections,
                HyperliquidWebSocketConnection::new,
            ),
            okx_public_rest: TypedConnectionCollection::new(
                &mut self.okx_public_rest_connections,
                OkxPublicRestConnection::new,
            ),
            okx_public_websocket: TypedConnectionCollection::new(
                &mut self.okx_public_websocket_connections,
                OkxPublicWebSocketConnection::new,
            ),
            okx_private_rest: TypedConnectionCollection::new(
                &mut self.okx_private_rest_connections,
                OkxPrivateRestConnection::new,
            ),
            okx_private_websocket: TypedConnectionCollection::new(
                &mut self.okx_private_websocket_connections,
                OkxPrivateWebSocketConnection::new,
            ),
        }
    }

    pub fn install_reference_contract(
        &mut self,
        key: impl Into<String>,
        client: ReferenceClient,
        stream: ReferenceEventStream,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        self.reference_clients
            .ensure_with(key.clone(), 1, || client)?;
        self.reference_clients
            .get_mut(&key)
            .expect("Reference client inserted")
            .set_state(ResourceState::Ready);
        self.reference_event_streams
            .ensure_with(key.clone(), 1, || stream)?;
        self.reference_event_streams
            .get_mut(&key)
            .expect("Reference event stream inserted")
            .set_state(ResourceState::Ready);
        Ok(())
    }

    pub(crate) fn reference_client_mut(&mut self, key: &str) -> Option<&mut ReferenceClient> {
        self.reference_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub(crate) fn register_timer(&mut self, name: String, period: Duration) {
        if period.is_zero() {
            let source = format!("timer:{name}");
            self.pending_system_events.push_back((
                source.clone(),
                SystemEvent::SourceFailed {
                    source,
                    error: "timer period must be positive".into(),
                },
            ));
            return;
        }
        self.timers.insert(
            name,
            SystemTimer {
                period,
                next: Instant::now() + period,
            },
        );
    }

    /// Starts every pre-installed streaming connection while it remains owned
    /// by this System. Provider lifecycle details stay in Integration; Conflux
    /// only advances managed state and publishes readiness evidence.
    pub(crate) async fn start_installed_connections(&mut self) {
        macro_rules! start_family {
            ($field:ident) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let Some(managed) = self.$field.get_mut(&key) else {
                        continue;
                    };
                    if managed.state() != ResourceState::Created {
                        continue;
                    }
                    managed.set_state(ResourceState::Starting);
                    let source = format!("integration:{key}");
                    match kairos_integration::ConnectionLifecycleCommand::connect(
                        managed.connection_mut(),
                    )
                    .await
                    {
                        Ok(()) => {
                            managed.set_state(ResourceState::Ready);
                            self.pending_system_events
                                .push_back((source.clone(), SystemEvent::SourceReady { source }));
                        }
                        Err(error) => {
                            managed.set_state(ResourceState::Failed);
                            self.pending_system_events.push_back((
                                source.clone(),
                                SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                },
                            ));
                        }
                    }
                }
            }};
        }

        start_family!(binance_spot_websocket_connections);
        start_family!(binance_spot_user_websocket_connections);
        start_family!(binance_margin_websocket_connections);
        start_family!(binance_margin_user_websocket_connections);
        start_family!(binance_usdm_websocket_connections);
        start_family!(binance_usdm_user_websocket_connections);
        start_family!(binance_coinm_websocket_connections);
        start_family!(binance_coinm_user_websocket_connections);
        start_family!(binance_options_websocket_connections);
        start_family!(binance_options_user_websocket_connections);
        start_family!(binance_stocks_websocket_connections);
        start_family!(binance_stocks_user_websocket_connections);
        start_family!(okx_public_websocket_connections);
        start_family!(okx_private_websocket_connections);
        start_family!(hyperliquid_websocket_connections);
        start_family!(ibkr_account_query_connections);
        start_family!(ibkr_account_stream_connections);
        start_family!(ibkr_order_connections);
        start_family!(ibkr_execution_stream_connections);
        start_family!(ibkr_market_data_connections);
        start_family!(massive_stocks_websocket_connections);
        start_family!(massive_options_websocket_connections);
    }

    /// Disconnects every long-lived connection before the System is dropped.
    /// Entries remain in their managed collections so shutdown never transfers
    /// ownership to a task or business module.
    pub(crate) async fn stop_connections(&mut self) {
        macro_rules! stop_family {
            ($field:ident) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let Some(managed) = self.$field.get_mut(&key) else {
                        continue;
                    };
                    if matches!(
                        managed.state(),
                        ResourceState::Created | ResourceState::Stopped | ResourceState::Retiring
                    ) {
                        continue;
                    }
                    managed.set_state(ResourceState::Stopping);
                    match kairos_integration::ConnectionLifecycleCommand::disconnect(
                        managed.connection_mut(),
                    )
                    .await
                    {
                        Ok(()) => managed.set_state(ResourceState::Stopped),
                        Err(_) => managed.set_state(ResourceState::Failed),
                    }
                }
            }};
        }

        stop_family!(binance_spot_websocket_connections);
        stop_family!(binance_spot_user_websocket_connections);
        stop_family!(binance_margin_websocket_connections);
        stop_family!(binance_margin_user_websocket_connections);
        stop_family!(binance_usdm_websocket_connections);
        stop_family!(binance_usdm_user_websocket_connections);
        stop_family!(binance_coinm_websocket_connections);
        stop_family!(binance_coinm_user_websocket_connections);
        stop_family!(binance_options_websocket_connections);
        stop_family!(binance_options_user_websocket_connections);
        stop_family!(binance_stocks_websocket_connections);
        stop_family!(binance_stocks_user_websocket_connections);
        stop_family!(okx_public_websocket_connections);
        stop_family!(okx_private_websocket_connections);
        stop_family!(hyperliquid_websocket_connections);
        stop_family!(ibkr_account_query_connections);
        stop_family!(ibkr_account_stream_connections);
        stop_family!(ibkr_order_connections);
        stop_family!(ibkr_execution_stream_connections);
        stop_family!(ibkr_market_data_connections);
        stop_family!(massive_stocks_websocket_connections);
        stop_family!(massive_options_websocket_connections);
    }

    pub(crate) fn update_timer(&mut self, now: Instant, state: &mut ConnectionDriverState) {
        for (name, timer) in &mut self.timers {
            if timer.next > now {
                continue;
            }
            let fired_at_unix_nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|value| u64::try_from(value.as_nanos()).unwrap_or(u64::MAX))
                .unwrap_or_default();
            state.push(
                format!("timer:{name}"),
                ConnectionDriverOutput::System(SystemEvent::Timer {
                    name: name.clone(),
                    fired_at_unix_nanos,
                }),
            );
            // Delay semantics: do not replay a burst of missed ticks.
            timer.next = now + timer.period;
        }
    }

    /// Polls every active event connection in one cooperative pass. This is a
    /// synchronous primitive for [`ConnectionDriver`](crate::process::ConnectionDriver);
    /// it is deliberately not a `tokio::select!` operand.
    pub(crate) fn poll_all_connection_next(
        &mut self,
        cx: &mut Context<'_>,
        state: &mut ConnectionDriverState,
    ) -> Poll<ConnectionDriverOutput> {
        while let Some((source, event)) = self.pending_system_events.pop_front() {
            state.push(source, ConnectionDriverOutput::System(event));
        }
        if let Some(output) = state.pop() {
            return Poll::Ready(output);
        }

        macro_rules! poll_family {
            ($field:ident, $trait:path, $map:expr, $family:literal) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let source = format!("integration:{}:{}", $family, key);
                    if state.occupied.contains(&source) {
                        continue;
                    }
                    let Some(managed) = self.$field.get_mut(&key) else {
                        continue;
                    };
                    if managed.state() != ResourceState::Ready {
                        continue;
                    }
                    let descriptor = managed.connection().descriptor().clone();
                    let generation = managed.generation();
                    match <_ as $trait>::poll_next(managed.connection_mut(), cx) {
                        Poll::Ready(Ok(event)) => {
                            state.push(
                                source,
                                ConnectionDriverOutput::Integration(IntegrationEvent {
                                    identity: ManagedConnectionIdentity {
                                        descriptor,
                                        generation,
                                    },
                                    event: ($map)(event),
                                }),
                            );
                        }
                        Poll::Ready(Err(error)) => {
                            managed.set_state(ResourceState::Degraded);
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        }
                        Poll::Pending => {}
                    }
                }
            }};
        }

        for offset in 0..CONNECTION_EVENT_FAMILIES {
            match (state.family_cursor + offset) % CONNECTION_EVENT_FAMILIES {
                0 => poll_family!(
                    binance_spot_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.spot.market"
                ),
                1 => poll_family!(
                    binance_spot_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.spot.user"
                ),
                2 => poll_family!(
                    binance_margin_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.margin.market"
                ),
                3 => poll_family!(
                    binance_margin_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.margin.user"
                ),
                4 => poll_family!(
                    binance_usdm_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.usdm.market"
                ),
                5 => poll_family!(
                    binance_usdm_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.usdm.user"
                ),
                6 => poll_family!(
                    binance_coinm_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.coinm.market"
                ),
                7 => poll_family!(
                    binance_coinm_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.coinm.user"
                ),
                8 => poll_family!(
                    binance_options_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.options.market"
                ),
                9 => poll_family!(
                    binance_options_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.options.user"
                ),
                10 => poll_family!(
                    binance_stocks_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "binance.stocks.market"
                ),
                11 => poll_family!(
                    binance_stocks_user_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "binance.stocks.user"
                ),
                12 => poll_family!(
                    okx_public_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "okx.public.market"
                ),
                13 => poll_family!(
                    okx_private_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "okx.private"
                ),
                14 => poll_family!(
                    hyperliquid_websocket_connections,
                    kairos_integration::ParticipantEventStream,
                    |event| event,
                    "hyperliquid.websocket"
                ),
                15 => poll_family!(
                    ibkr_account_stream_connections,
                    kairos_integration::AccountStream,
                    kairos_integration::ExternalParticipantEvent::Account,
                    "ibkr.account"
                ),
                16 => poll_family!(
                    ibkr_execution_stream_connections,
                    kairos_integration::ExecutionStream,
                    kairos_integration::ExternalParticipantEvent::Execution,
                    "ibkr.execution"
                ),
                17 => poll_family!(
                    massive_stocks_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.stocks"
                ),
                18 => poll_family!(
                    massive_options_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.options"
                ),
                _ => unreachable!(),
            }
        }
        state.family_cursor = (state.family_cursor + 1) % CONNECTION_EVENT_FAMILIES;

        self.poll_all_connection_maintenance(cx, Instant::now(), state);
        self.poll_all_contract_events(cx, state);
        state.pop().map_or(Poll::Pending, Poll::Ready)
    }

    fn poll_all_contract_events(
        &mut self,
        cx: &mut Context<'_>,
        state: &mut ConnectionDriverState,
    ) {
        macro_rules! poll_contract {
            ($field:ident, $variant:ident, $family:literal) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let source = format!("aeron:{}:{}", $family, key);
                    if state.occupied.contains(&source) {
                        continue;
                    }
                    let Some(managed) = self.$field.get_mut(&key) else {
                        continue;
                    };
                    if managed.state() != ResourceState::Ready {
                        continue;
                    }
                    match futures_util::Stream::poll_next(
                        std::pin::Pin::new(managed.resource_mut()),
                        cx,
                    ) {
                        Poll::Ready(Some(Ok(frame))) => state.push(
                            source,
                            ConnectionDriverOutput::$variant { client: key, frame },
                        ),
                        Poll::Ready(Some(Err(error))) => {
                            managed.set_state(ResourceState::Degraded);
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        }
                        Poll::Ready(None) => managed.set_state(ResourceState::Stopped),
                        Poll::Pending => {}
                    }
                }
            }};
        }

        const FAMILIES: usize = 5;
        for offset in 0..FAMILIES {
            match (state.contract_family_cursor + offset) % FAMILIES {
                0 => poll_contract!(account_event_streams, Account, "account"),
                1 => poll_contract!(execution_event_streams, Execution, "execution"),
                2 => poll_contract!(market_event_streams, Market, "market"),
                3 => poll_contract!(reference_event_streams, Reference, "reference"),
                4 => poll_contract!(risk_event_streams, Risk, "risk"),
                _ => unreachable!(),
            }
        }
        state.contract_family_cursor = (state.contract_family_cursor + 1) % FAMILIES;
    }

    fn poll_all_connection_maintenance(
        &mut self,
        cx: &mut Context<'_>,
        now: Instant,
        state: &mut ConnectionDriverState,
    ) {
        macro_rules! poll_maintenance {
            ($field:ident, $family:literal) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let source = format!("maintenance:{}:{}", $family, key);
                    let Some(managed) = self.$field.get_mut(&key) else {
                        state.maintenance_in_progress.remove(&source);
                        continue;
                    };
                    if managed.state() != ResourceState::Ready
                        && managed.state() != ResourceState::Degraded
                    {
                        continue;
                    }
                    let due = state.maintenance_in_progress.contains(&source)
                        || kairos_integration::ConnectionMaintenance::next_maintenance_at(
                            managed.connection(),
                        )
                        .is_some_and(|deadline| deadline <= now);
                    if !due {
                        continue;
                    }
                    match kairos_integration::ConnectionMaintenance::poll_maintenance(
                        managed.connection_mut(),
                        cx,
                        now,
                    ) {
                        Poll::Pending => {
                            state.maintenance_in_progress.insert(source);
                        }
                        Poll::Ready(Ok(kairos_integration::MaintenanceOutcome::Healthy))
                        | Poll::Ready(Ok(kairos_integration::MaintenanceOutcome::Progressed)) => {
                            state.maintenance_in_progress.remove(&source);
                        }
                        Poll::Ready(Ok(
                            kairos_integration::MaintenanceOutcome::ReconnectRequired { reason },
                        )) => {
                            state.maintenance_in_progress.remove(&source);
                            managed.set_state(ResourceState::Degraded);
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: reason,
                                }),
                            );
                        }
                        Poll::Ready(Err(error)) => {
                            state.maintenance_in_progress.remove(&source);
                            managed.set_state(ResourceState::Degraded);
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        }
                    }
                }
            }};
        }

        poll_maintenance!(binance_spot_websocket_connections, "binance.spot.market");
        poll_maintenance!(binance_spot_user_websocket_connections, "binance.spot.user");
        poll_maintenance!(
            binance_margin_websocket_connections,
            "binance.margin.market"
        );
        poll_maintenance!(
            binance_margin_user_websocket_connections,
            "binance.margin.user"
        );
        poll_maintenance!(binance_usdm_websocket_connections, "binance.usdm.market");
        poll_maintenance!(binance_usdm_user_websocket_connections, "binance.usdm.user");
        poll_maintenance!(binance_coinm_websocket_connections, "binance.coinm.market");
        poll_maintenance!(
            binance_coinm_user_websocket_connections,
            "binance.coinm.user"
        );
        poll_maintenance!(
            binance_options_websocket_connections,
            "binance.options.market"
        );
        poll_maintenance!(
            binance_options_user_websocket_connections,
            "binance.options.user"
        );
        poll_maintenance!(
            binance_stocks_websocket_connections,
            "binance.stocks.market"
        );
        poll_maintenance!(
            binance_stocks_user_websocket_connections,
            "binance.stocks.user"
        );
        poll_maintenance!(okx_public_websocket_connections, "okx.public.market");
        poll_maintenance!(okx_private_websocket_connections, "okx.private");
        poll_maintenance!(hyperliquid_websocket_connections, "hyperliquid.websocket");
        poll_maintenance!(ibkr_account_query_connections, "ibkr.account.query");
        poll_maintenance!(ibkr_account_stream_connections, "ibkr.account");
        poll_maintenance!(ibkr_order_connections, "ibkr.order");
        poll_maintenance!(ibkr_execution_stream_connections, "ibkr.execution");
        poll_maintenance!(ibkr_market_data_connections, "ibkr.market");
        poll_maintenance!(massive_stocks_websocket_connections, "massive.stocks");
        poll_maintenance!(massive_options_websocket_connections, "massive.options");
    }

    pub(crate) fn next_wakeup_deadline(&self) -> Option<Instant> {
        let mut deadline = if self.pending_system_events.is_empty() {
            self.timers.values().map(|timer| timer.next).min()
        } else {
            Some(Instant::now())
        };
        macro_rules! visit {
            ($field:ident) => {
                for (_, managed) in self.$field.iter() {
                    if let Some(candidate) =
                        kairos_integration::ConnectionMaintenance::next_maintenance_at(
                            managed.connection(),
                        )
                    {
                        deadline =
                            Some(deadline.map_or(candidate, |value: Instant| value.min(candidate)));
                    }
                }
            };
        }
        visit!(binance_spot_websocket_connections);
        visit!(binance_spot_user_websocket_connections);
        visit!(binance_margin_websocket_connections);
        visit!(binance_margin_user_websocket_connections);
        visit!(binance_usdm_websocket_connections);
        visit!(binance_usdm_user_websocket_connections);
        visit!(binance_coinm_websocket_connections);
        visit!(binance_coinm_user_websocket_connections);
        visit!(binance_options_websocket_connections);
        visit!(binance_options_user_websocket_connections);
        visit!(binance_stocks_websocket_connections);
        visit!(binance_stocks_user_websocket_connections);
        visit!(okx_public_websocket_connections);
        visit!(okx_private_websocket_connections);
        visit!(hyperliquid_websocket_connections);
        visit!(ibkr_account_query_connections);
        visit!(ibkr_account_stream_connections);
        visit!(ibkr_order_connections);
        visit!(ibkr_execution_stream_connections);
        visit!(ibkr_market_data_connections);
        visit!(massive_stocks_websocket_connections);
        visit!(massive_options_websocket_connections);
        deadline
    }
}

impl Default for ConfluxSystem {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use kairos_transport::SnapshotEnvelopeMetadata;

    use super::*;

    #[test]
    fn mmap_reader_and_writer_are_named_managed_resources() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("risk.latest.mmap");
        let mut system = ConfluxSystem::new();
        let key = "risk-latest".to_owned();
        let writer = SharedSnapshotWriter::create(&path, 1_024).unwrap();
        system
            .mmap_writers
            .ensure_with(key.clone(), 1, || writer)
            .unwrap();
        let writer = system.mmap_writers.get_mut(&key).unwrap();
        writer
            .resource_mut()
            .publish_with_metadata(
                SnapshotEnvelopeMetadata {
                    resource_epoch: 1,
                    producer_incarnation: 1,
                    generation: 1,
                    applied_event_sequence: 7,
                    published_at_unix_nanos: 10,
                },
                b"risk-view",
            )
            .unwrap();
        writer.set_state(crate::ResourceState::Ready);

        let reader = SharedSnapshotReader::open(&path).unwrap();
        system
            .mmap_readers
            .ensure_with(key.clone(), 1, || reader)
            .unwrap();
        let frame = system
            .mmap_readers
            .get(&key)
            .unwrap()
            .resource()
            .read_payload()
            .unwrap();
        assert_eq!(frame.payload, b"risk-view");
        assert_eq!(frame.applied_event_sequence, 7);
        assert_eq!(
            system.mmap_writers.get(&key).unwrap().state(),
            crate::ResourceState::Ready
        );
    }

    #[test]
    fn typed_creation_uses_connection_key_as_the_only_okx_identity() {
        let mut system = ConfluxSystem::new();
        let key = ConnectionKey::new("reference-main").unwrap();
        system
            .connections()
            .okx_public_rest
            .create(
                key.clone(),
                kairos_integration::participants::okx::OkxRestConfig {
                    environment: "test".into(),
                    endpoint: "https://www.okx.com".into(),
                },
            )
            .unwrap();

        let descriptor = system
            .connections()
            .okx_public_rest
            .get(&key)
            .unwrap()
            .descriptor()
            .clone();
        assert_eq!(descriptor.connection_key, key);
    }
}
