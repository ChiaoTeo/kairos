use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::marker::PhantomData;
use std::path::{Path, PathBuf};
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use kairos_account_contract::{AccountClient, AccountEventStream};
use kairos_capital_contract::{CapitalClient, CapitalEventStream};
use kairos_execution_contract::{ExecutionClient, ExecutionEventStream};
use kairos_integration::ConnectionKey;
use kairos_integration::participants::binance::advanced::stocks::{
    BinanceStocksRestConnection, BinanceStocksUserWebSocketConnection,
    BinanceStocksWebSocketConnection,
};
use kairos_integration::participants::binance::capital::{
    BinanceCapitalRestConfig, BinanceCapitalRestConnection, BinanceSubAccountCapitalRestConfig,
    BinanceSubAccountCapitalRestConnection,
};
use kairos_integration::participants::binance::coinm::{
    BinanceCoinMRestConnection, BinanceCoinMUserWebSocketConnection,
    BinanceCoinMWebSocketConnection,
};
use kairos_integration::participants::binance::earn::BinanceSimpleEarnRestConnection;
use kairos_integration::participants::binance::funding::BinanceFundingRestConnection;
use kairos_integration::participants::binance::margin::{
    BinanceMarginRestConnection, BinanceMarginUserWebSocketConnection,
    BinanceMarginWebSocketConnection,
};
use kairos_integration::participants::binance::options::{
    BinanceOptionsRestConnection, BinanceOptionsUserWebSocketConnection,
    BinanceOptionsWebSocketConnection,
};
use kairos_integration::participants::binance::spot::{
    BinanceSpotRestConnection, BinanceSpotUserWebSocketConnection, BinanceSpotWebSocketConnection,
};
use kairos_integration::participants::binance::usdm::{
    BinanceUsdMRestConnection, BinanceUsdMUserWebSocketConnection, BinanceUsdMWebSocketConnection,
};
use kairos_integration::participants::binance::{
    BinanceRestConfig, BinanceUserWebSocketConfig, BinanceWebSocketConfig,
};
use kairos_integration::participants::hyperliquid::info::HyperliquidInfoRestConnection;
use kairos_integration::participants::hyperliquid::{
    HyperliquidRestConfig, HyperliquidWebSocketConfig, HyperliquidWebSocketConnection,
};
use kairos_integration::participants::ibkr::{
    IbkrAccountQueryConfig, IbkrAccountQueryConnection, IbkrAccountStreamConfig,
    IbkrAccountStreamConnection, IbkrExecutionStreamConfig, IbkrExecutionStreamConnection,
    IbkrMarketDataConfig, IbkrMarketDataConnection, IbkrOrderConfig, IbkrOrderConnection,
};
use kairos_integration::participants::massive::{
    MassiveCryptoWebSocketConnection, MassiveForexWebSocketConnection,
    MassiveFuturesWebSocketConnection, MassiveIndicesWebSocketConnection,
    MassiveOptionsWebSocketConnection, MassiveRestConfig, MassiveRestConnection,
    MassiveStocksWebSocketConnection, MassiveWebSocketConfig,
};
use kairos_integration::participants::okx::private::{
    OkxPrivateRestConnection, OkxPrivateWebSocketConnection,
};
use kairos_integration::participants::okx::public::{
    OkxPublicRestConnection, OkxPublicWebSocketConnection,
};
use kairos_market_contract::{MarketClient, MarketEventStream};
use kairos_reference_contract::{ReferenceClient, ReferenceEventStream};
use kairos_risk_contract::{RiskClient, RiskEventStream};
use kairos_transport::AeronBytePublisher;
use thiserror::Error;
use tokio::time::Instant;

use crate::resource::{ManagedConnections, ManagedLifecycleOperation};
use crate::{
    IntegrationEvent, ManagedClients, ManagedConnectionIdentity, NamedResources, ResourceState,
    SystemEvent,
};

pub fn reference_connection_from_workspace(
    workspace: &kairos_workspace::Workspace,
    aeron_dir: Option<&Path>,
    aeron_channel: &str,
) -> Result<kairos_reference_contract::ReferenceConnection, String> {
    Ok(kairos_reference_contract::ReferenceConnection {
        contract: kairos_protocol::ContractClient::new(
            workspace
                .control_socket("reference")
                .map_err(|error| error.to_string())?,
            None::<std::path::PathBuf>,
            Some(
                kairos_transport::AeronEndpoint::new(
                    aeron_dir.map(Path::to_path_buf),
                    aeron_channel,
                    kairos_transport::stream_ids::REFERENCE_CHANGES,
                )
                .map_err(|error| error.to_string())?,
            ),
        ),
        database: workspace
            .child(&["state", "reference", "reference.sqlite"])
            .map_err(|error| error.to_string())?,
        actor_id: kairos_primitives::runtime::ActorId::new("reference-actor")
            .map_err(|error| error.to_string())?,
    })
}

pub(crate) enum ConnectionDriverOutput {
    Integration(IntegrationEvent),
    System(SystemEvent),
    Account {
        client: String,
        frame: kairos_account_contract::AccountEventFrame,
    },
    Capital {
        client: String,
        frame: kairos_capital_contract::CapitalEventFrame,
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
    LifecycleStopped {
        collection: &'static str,
        key: String,
        result: Result<(), String>,
    },
}

pub(crate) struct ConnectionDriverState {
    family_cursor: usize,
    contract_family_cursor: usize,
    ready: VecDeque<(String, ConnectionDriverOutput)>,
    occupied: HashSet<String>,
    maintenance_in_progress: HashSet<String>,
    lifecycle_retry_at: HashMap<String, Instant>,
    lifecycle_attempts: HashMap<String, u32>,
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
            lifecycle_retry_at: HashMap::new(),
            lifecycle_attempts: HashMap::new(),
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

    fn retry_due(&self, source: &str, now: Instant) -> bool {
        self.lifecycle_retry_at
            .get(source)
            .is_some_and(|deadline| *deadline <= now)
    }

    fn schedule_retry(
        &mut self,
        source: &str,
        now: Instant,
        policy: crate::RecoveryPolicy,
    ) -> bool {
        let attempt = self
            .lifecycle_attempts
            .get(source)
            .copied()
            .unwrap_or_default()
            .saturating_add(1);
        if policy
            .maximum_attempts
            .is_some_and(|maximum| attempt > maximum)
        {
            self.lifecycle_retry_at.remove(source);
            return false;
        }
        self.lifecycle_attempts.insert(source.to_owned(), attempt);
        let multiplier = 1_u32 << attempt.saturating_sub(1).min(31);
        let delay = policy
            .initial_backoff
            .saturating_mul(multiplier)
            .min(policy.maximum_backoff);
        self.lifecycle_retry_at
            .insert(source.to_owned(), now + delay);
        true
    }

    fn clear_retry(&mut self, source: &str) {
        self.lifecycle_retry_at.remove(source);
        self.lifecycle_attempts.remove(source);
    }

    fn pending_maintenance_deadline(
        &self,
        source: &str,
        deadline: Option<Instant>,
    ) -> Option<Instant> {
        if self.maintenance_in_progress.contains(source) {
            None
        } else {
            deadline
        }
    }

    pub(crate) fn purge_integration_identity(&mut self, identity: &ManagedConnectionIdentity) {
        self.ready.retain(|(_, output)| {
            !matches!(
                output,
                ConnectionDriverOutput::Integration(event) if event.identity == *identity
            ) && !matches!(
                output,
                ConnectionDriverOutput::System(SystemEvent::ConnectionStateChanged {
                    connection,
                    ..
                }) if connection == identity
            )
        });
        self.occupied = self
            .ready
            .iter()
            .map(|(source, _)| source.clone())
            .collect();
    }

    pub(crate) fn clear_removed_connection(&mut self, collection: &str, key: &str) {
        self.clear_retry(&format!("lifecycle:{collection}:{key}"));
        let family = match collection {
            "binance_spot_websocket_connections" => "binance.spot.market",
            "binance_spot_user_websocket_connections" => "binance.spot.user",
            "binance_margin_websocket_connections" => "binance.margin.market",
            "binance_margin_user_websocket_connections" => "binance.margin.user",
            "binance_usdm_websocket_connections" => "binance.usdm.market",
            "binance_usdm_user_websocket_connections" => "binance.usdm.user",
            "binance_coinm_websocket_connections" => "binance.coinm.market",
            "binance_coinm_user_websocket_connections" => "binance.coinm.user",
            "binance_options_websocket_connections" => "binance.options.market",
            "binance_options_user_websocket_connections" => "binance.options.user",
            "binance_stocks_websocket_connections" => "binance.stocks.market",
            "binance_stocks_user_websocket_connections" => "binance.stocks.user",
            "okx_public_websocket_connections" => "okx.public.market",
            "okx_private_websocket_connections" => "okx.private",
            "hyperliquid_websocket_connections" => "hyperliquid.websocket",
            "ibkr_account_query_connections" => "ibkr.account.query",
            "ibkr_account_stream_connections" => "ibkr.account",
            "ibkr_order_connections" => "ibkr.order",
            "ibkr_execution_stream_connections" => "ibkr.execution",
            "ibkr_market_data_connections" => "ibkr.market",
            "massive_stocks_websocket_connections" => "massive.stocks",
            "massive_options_websocket_connections" => "massive.options",
            "massive_futures_websocket_connections" => "massive.futures",
            "massive_indices_websocket_connections" => "massive.indices",
            "massive_forex_websocket_connections" => "massive.forex",
            "massive_crypto_websocket_connections" => "massive.crypto",
            _ => return,
        };
        self.maintenance_in_progress
            .remove(&format!("maintenance:{family}:{key}"));
    }
}

const CONNECTION_EVENT_FAMILIES: usize = 24;

fn is_permanent_connection_error(error: &kairos_integration::IntegrationError) -> bool {
    matches!(
        error,
        kairos_integration::IntegrationError::InvalidRequest(_)
            | kairos_integration::IntegrationError::Authentication(_)
            | kairos_integration::IntegrationError::Authorization(_)
            | kairos_integration::IntegrationError::Entitlement(_)
            | kairos_integration::IntegrationError::UnsupportedOperation
    )
}

fn is_recoverable_connection_error(error: &kairos_integration::IntegrationError) -> bool {
    matches!(
        error,
        kairos_integration::IntegrationError::NotReady
            | kairos_integration::IntegrationError::RateLimited(_)
            | kairos_integration::IntegrationError::Transport(_)
            | kairos_integration::IntegrationError::Unavailable(_)
    )
}

fn participant_event_identity(
    event: &kairos_integration::ExternalParticipantEvent,
) -> Option<(&kairos_integration::ParticipantRef, &ConnectionKey)> {
    match event {
        kairos_integration::ExternalParticipantEvent::Account(event) => {
            Some((&event.participant, &event.connection_key))
        },
        kairos_integration::ExternalParticipantEvent::Execution(event) => {
            Some((&event.participant, &event.connection_key))
        },
        kairos_integration::ExternalParticipantEvent::Market(_) => None,
    }
}

#[derive(Debug, Error)]
pub enum ConnectionAccessError {
    #[error("connection `{0}` does not exist")]
    NotFound(ConnectionKey),
    #[error("connection `{0}` is retiring")]
    Retiring(ConnectionKey),
    #[error("connection `{0}` is not ready")]
    NotReady(ConnectionKey),
}

#[derive(Debug, Error)]
pub enum ConnectionCreateError {
    #[error("connection `{0}` already exists")]
    AlreadyExists(ConnectionKey),
    #[error("invalid connection create options: {0}")]
    InvalidOptions(String),
    #[error(transparent)]
    Integration(#[from] kairos_integration::IntegrationError),
    #[error(transparent)]
    Resource(#[from] crate::ResourceError),
}

pub struct TypedConnectionCollection<'a, C, P> {
    connections: &'a mut ManagedConnections<String, C>,
    constructor: fn(ConnectionKey, P) -> Result<C, kairos_integration::IntegrationError>,
    requires_ready: bool,
    parameters: PhantomData<fn(P)>,
}

impl<'a, C, P> TypedConnectionCollection<'a, C, P> {
    fn new(
        connections: &'a mut ManagedConnections<String, C>,
        constructor: fn(ConnectionKey, P) -> Result<C, kairos_integration::IntegrationError>,
        requires_ready: bool,
    ) -> Self {
        Self {
            connections,
            constructor,
            requires_ready,
            parameters: PhantomData,
        }
    }

    pub fn create(
        &mut self,
        key: ConnectionKey,
        parameters: P,
    ) -> Result<(), ConnectionCreateError> {
        self.create_with_options(key, parameters, crate::ConnectionCreateOptions::default())
    }

    pub fn create_with_options(
        &mut self,
        key: ConnectionKey,
        parameters: P,
        options: crate::ConnectionCreateOptions,
    ) -> Result<(), ConnectionCreateError> {
        if options.recovery.initial_backoff.is_zero()
            || options.recovery.maximum_backoff < options.recovery.initial_backoff
        {
            return Err(ConnectionCreateError::InvalidOptions(
                "recovery backoff must be positive and maximum_backoff must not be smaller than initial_backoff"
                    .into(),
            ));
        }
        if self.connections.get(&key.to_string()).is_some() {
            return Err(ConnectionCreateError::AlreadyExists(key));
        }
        let connection = (self.constructor)(key.clone(), parameters)?;
        if !self
            .connections
            .insert_new_with_options(key.to_string(), connection, options)?
        {
            return Err(ConnectionCreateError::AlreadyExists(key));
        }
        Ok(())
    }

    pub fn get(&mut self, key: &ConnectionKey) -> Result<&mut C, ConnectionAccessError> {
        let managed = self
            .connections
            .get_mut(&key.to_string())
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))?;
        Self::validate_access(key, managed.state(), self.requires_ready)?;
        Ok(managed.connection_mut())
    }

    /// Borrow a managed connection for a capability whose query methods are
    /// read-only. The borrow prevents lifecycle mutation until queries finish.
    pub fn get_shared(&self, key: &ConnectionKey) -> Result<&C, ConnectionAccessError> {
        let managed = self
            .connections
            .get(&key.to_string())
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))?;
        Self::validate_access(key, managed.state(), self.requires_ready)?;
        Ok(managed.connection())
    }

    fn validate_access(
        key: &ConnectionKey,
        state: ResourceState,
        requires_ready: bool,
    ) -> Result<(), ConnectionAccessError> {
        if state == ResourceState::Retiring {
            return Err(ConnectionAccessError::Retiring(key.clone()));
        }
        if (requires_ready && state != ResourceState::Ready)
            || matches!(
                state,
                ResourceState::Starting
                    | ResourceState::Failed
                    | ResourceState::Stopping
                    | ResourceState::Stopped
            )
        {
            return Err(ConnectionAccessError::NotReady(key.clone()));
        }
        Ok(())
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

    fn remove_now(&mut self, key: &ConnectionKey) -> Result<(), ConnectionAccessError> {
        self.connections
            .remove(&key.to_string())
            .map(drop)
            .ok_or_else(|| ConnectionAccessError::NotFound(key.clone()))
    }
}

impl TypedConnectionCollection<'_, MassiveRestConnection, MassiveRestConfig> {
    /// Removes a bounded HTTP client that has no lifecycle or event poller.
    /// Stateful connections can only be retired through Conflux control.
    pub fn remove(&mut self, key: &ConnectionKey) -> Result<(), ConnectionAccessError> {
        self.remove_now(key)
    }
}

macro_rules! impl_remove_bounded_rest_connection {
    ($connection:ty, $config:ty) => {
        impl TypedConnectionCollection<'_, $connection, $config> {
            /// Removes a bounded HTTP client that has no lifecycle or event poller.
            pub fn remove(&mut self, key: &ConnectionKey) -> Result<(), ConnectionAccessError> {
                self.remove_now(key)
            }
        }
    };
}

impl_remove_bounded_rest_connection!(BinanceSpotRestConnection, BinanceRestConfig);
impl_remove_bounded_rest_connection!(BinanceUsdMRestConnection, BinanceRestConfig);
impl_remove_bounded_rest_connection!(BinanceCoinMRestConnection, BinanceRestConfig);
impl_remove_bounded_rest_connection!(BinanceOptionsRestConnection, BinanceRestConfig);
impl_remove_bounded_rest_connection!(BinanceStocksRestConnection, BinanceRestConfig);
impl_remove_bounded_rest_connection!(HyperliquidInfoRestConnection, HyperliquidRestConfig);
impl_remove_bounded_rest_connection!(
    OkxPublicRestConnection,
    kairos_integration::participants::okx::OkxRestConfig
);

pub struct ConnectionCollections<'a> {
    pub binance_capital_rest:
        TypedConnectionCollection<'a, BinanceCapitalRestConnection, BinanceCapitalRestConfig>,
    pub binance_subaccount_capital_rest: TypedConnectionCollection<
        'a,
        BinanceSubAccountCapitalRestConnection,
        BinanceSubAccountCapitalRestConfig,
    >,
    pub binance_spot_rest:
        TypedConnectionCollection<'a, BinanceSpotRestConnection, BinanceRestConfig>,
    pub binance_funding_rest:
        TypedConnectionCollection<'a, BinanceFundingRestConnection, BinanceRestConfig>,
    pub binance_earn_rest:
        TypedConnectionCollection<'a, BinanceSimpleEarnRestConnection, BinanceRestConfig>,
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
    pub massive_futures_websocket:
        TypedConnectionCollection<'a, MassiveFuturesWebSocketConnection, MassiveWebSocketConfig>,
    pub massive_indices_websocket:
        TypedConnectionCollection<'a, MassiveIndicesWebSocketConnection, MassiveWebSocketConfig>,
    pub massive_forex_websocket:
        TypedConnectionCollection<'a, MassiveForexWebSocketConnection, MassiveWebSocketConfig>,
    pub massive_crypto_websocket:
        TypedConnectionCollection<'a, MassiveCryptoWebSocketConnection, MassiveWebSocketConfig>,
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
    pub capital_clients: ManagedClients<String, CapitalClient>,
    pub execution_clients: ManagedClients<String, ExecutionClient>,
    pub market_clients: ManagedClients<String, MarketClient>,
    pub reference_clients: ManagedClients<String, ReferenceClient>,
    pub risk_clients: ManagedClients<String, RiskClient>,

    pub account_event_streams: NamedResources<String, AccountEventStream>,
    pub capital_event_streams: NamedResources<String, CapitalEventStream>,
    pub execution_event_streams: NamedResources<String, ExecutionEventStream>,
    pub market_event_streams: NamedResources<String, MarketEventStream>,
    pub reference_event_streams: NamedResources<String, ReferenceEventStream>,
    pub risk_event_streams: NamedResources<String, RiskEventStream>,

    /// Process-owned transport resources. These collections manage concrete
    /// Aeron and indexed-view handles without pretending that their byte APIs are a
    /// business Contract. Contract codecs remain owned by each module.
    aeron_publishers: NamedResources<String, AeronBytePublisher>,
    indexed_writers: NamedResources<String, kairos_indexed_view::IndexedViewWriter>,

    pub(crate) binance_spot_rest_connections: ManagedConnections<String, BinanceSpotRestConnection>,
    pub(crate) binance_capital_rest_connections:
        ManagedConnections<String, BinanceCapitalRestConnection>,
    pub(crate) binance_subaccount_capital_rest_connections:
        ManagedConnections<String, BinanceSubAccountCapitalRestConnection>,
    pub(crate) binance_spot_websocket_connections:
        ManagedConnections<String, BinanceSpotWebSocketConnection>,
    pub(crate) binance_spot_user_websocket_connections:
        ManagedConnections<String, BinanceSpotUserWebSocketConnection>,
    pub(crate) binance_funding_rest_connections:
        ManagedConnections<String, BinanceFundingRestConnection>,
    pub(crate) binance_earn_rest_connections:
        ManagedConnections<String, BinanceSimpleEarnRestConnection>,
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
    pub(crate) massive_futures_websocket_connections:
        ManagedConnections<String, MassiveFuturesWebSocketConnection>,
    pub(crate) massive_indices_websocket_connections:
        ManagedConnections<String, MassiveIndicesWebSocketConnection>,
    pub(crate) massive_forex_websocket_connections:
        ManagedConnections<String, MassiveForexWebSocketConnection>,
    pub(crate) massive_crypto_websocket_connections:
        ManagedConnections<String, MassiveCryptoWebSocketConnection>,
}

impl ConfluxSystem {
    pub fn new() -> Self {
        Self {
            timers: BTreeMap::new(),
            pending_system_events: VecDeque::new(),
            account_clients: ManagedClients::new(),
            capital_clients: ManagedClients::new(),
            execution_clients: ManagedClients::new(),
            market_clients: ManagedClients::new(),
            reference_clients: ManagedClients::new(),
            risk_clients: ManagedClients::new(),
            account_event_streams: NamedResources::new(),
            capital_event_streams: NamedResources::new(),
            execution_event_streams: NamedResources::new(),
            market_event_streams: NamedResources::new(),
            reference_event_streams: NamedResources::new(),
            risk_event_streams: NamedResources::new(),
            aeron_publishers: NamedResources::new(),
            indexed_writers: NamedResources::new(),
            binance_spot_rest_connections: ManagedConnections::new(),
            binance_capital_rest_connections: ManagedConnections::new(),
            binance_subaccount_capital_rest_connections: ManagedConnections::new(),
            binance_spot_websocket_connections: ManagedConnections::new(),
            binance_spot_user_websocket_connections: ManagedConnections::new(),
            binance_funding_rest_connections: ManagedConnections::new(),
            binance_earn_rest_connections: ManagedConnections::new(),
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
            massive_futures_websocket_connections: ManagedConnections::new(),
            massive_indices_websocket_connections: ManagedConnections::new(),
            massive_forex_websocket_connections: ManagedConnections::new(),
            massive_crypto_websocket_connections: ManagedConnections::new(),
        }
    }

    pub fn connections(&mut self) -> ConnectionCollections<'_> {
        ConnectionCollections {
            binance_capital_rest: TypedConnectionCollection::new(
                &mut self.binance_capital_rest_connections,
                BinanceCapitalRestConnection::new,
                false,
            ),
            binance_subaccount_capital_rest: TypedConnectionCollection::new(
                &mut self.binance_subaccount_capital_rest_connections,
                BinanceSubAccountCapitalRestConnection::new,
                false,
            ),
            binance_spot_rest: TypedConnectionCollection::new(
                &mut self.binance_spot_rest_connections,
                BinanceSpotRestConnection::new,
                false,
            ),
            binance_funding_rest: TypedConnectionCollection::new(
                &mut self.binance_funding_rest_connections,
                BinanceFundingRestConnection::new,
                false,
            ),
            binance_earn_rest: TypedConnectionCollection::new(
                &mut self.binance_earn_rest_connections,
                BinanceSimpleEarnRestConnection::new,
                false,
            ),
            binance_margin_rest: TypedConnectionCollection::new(
                &mut self.binance_margin_rest_connections,
                BinanceMarginRestConnection::new,
                false,
            ),
            binance_usdm_rest: TypedConnectionCollection::new(
                &mut self.binance_usdm_rest_connections,
                BinanceUsdMRestConnection::new,
                false,
            ),
            binance_coinm_rest: TypedConnectionCollection::new(
                &mut self.binance_coinm_rest_connections,
                BinanceCoinMRestConnection::new,
                false,
            ),
            binance_options_rest: TypedConnectionCollection::new(
                &mut self.binance_options_rest_connections,
                BinanceOptionsRestConnection::new,
                false,
            ),
            binance_stocks_rest: TypedConnectionCollection::new(
                &mut self.binance_stocks_rest_connections,
                BinanceStocksRestConnection::new,
                false,
            ),
            binance_spot_websocket: TypedConnectionCollection::new(
                &mut self.binance_spot_websocket_connections,
                BinanceSpotWebSocketConnection::new,
                true,
            ),
            binance_usdm_websocket: TypedConnectionCollection::new(
                &mut self.binance_usdm_websocket_connections,
                BinanceUsdMWebSocketConnection::new,
                true,
            ),
            binance_coinm_websocket: TypedConnectionCollection::new(
                &mut self.binance_coinm_websocket_connections,
                BinanceCoinMWebSocketConnection::new,
                true,
            ),
            binance_options_websocket: TypedConnectionCollection::new(
                &mut self.binance_options_websocket_connections,
                BinanceOptionsWebSocketConnection::new,
                true,
            ),
            binance_stocks_websocket: TypedConnectionCollection::new(
                &mut self.binance_stocks_websocket_connections,
                BinanceStocksWebSocketConnection::new,
                true,
            ),
            binance_spot_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_spot_user_websocket_connections,
                BinanceSpotUserWebSocketConnection::new,
                true,
            ),
            binance_margin_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_margin_user_websocket_connections,
                BinanceMarginUserWebSocketConnection::new,
                true,
            ),
            binance_usdm_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_usdm_user_websocket_connections,
                BinanceUsdMUserWebSocketConnection::new,
                true,
            ),
            binance_coinm_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_coinm_user_websocket_connections,
                BinanceCoinMUserWebSocketConnection::new,
                true,
            ),
            binance_options_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_options_user_websocket_connections,
                BinanceOptionsUserWebSocketConnection::new,
                true,
            ),
            binance_stocks_user_websocket: TypedConnectionCollection::new(
                &mut self.binance_stocks_user_websocket_connections,
                BinanceStocksUserWebSocketConnection::new,
                true,
            ),
            ibkr_account_query: TypedConnectionCollection::new(
                &mut self.ibkr_account_query_connections,
                IbkrAccountQueryConnection::new,
                true,
            ),
            ibkr_account_stream: TypedConnectionCollection::new(
                &mut self.ibkr_account_stream_connections,
                IbkrAccountStreamConnection::new,
                true,
            ),
            ibkr_order: TypedConnectionCollection::new(
                &mut self.ibkr_order_connections,
                IbkrOrderConnection::new,
                true,
            ),
            ibkr_execution_stream: TypedConnectionCollection::new(
                &mut self.ibkr_execution_stream_connections,
                IbkrExecutionStreamConnection::new,
                true,
            ),
            ibkr_market_data: TypedConnectionCollection::new(
                &mut self.ibkr_market_data_connections,
                IbkrMarketDataConnection::new,
                true,
            ),
            hyperliquid_info_rest: TypedConnectionCollection::new(
                &mut self.hyperliquid_info_rest_connections,
                HyperliquidInfoRestConnection::new,
                false,
            ),
            massive_rest: TypedConnectionCollection::new(
                &mut self.massive_rest_connections,
                MassiveRestConnection::new,
                false,
            ),
            massive_stocks_websocket: TypedConnectionCollection::new(
                &mut self.massive_stocks_websocket_connections,
                MassiveStocksWebSocketConnection::new,
                true,
            ),
            massive_options_websocket: TypedConnectionCollection::new(
                &mut self.massive_options_websocket_connections,
                MassiveOptionsWebSocketConnection::new,
                true,
            ),
            massive_futures_websocket: TypedConnectionCollection::new(
                &mut self.massive_futures_websocket_connections,
                MassiveFuturesWebSocketConnection::new,
                true,
            ),
            massive_indices_websocket: TypedConnectionCollection::new(
                &mut self.massive_indices_websocket_connections,
                MassiveIndicesWebSocketConnection::new,
                true,
            ),
            massive_forex_websocket: TypedConnectionCollection::new(
                &mut self.massive_forex_websocket_connections,
                MassiveForexWebSocketConnection::new,
                true,
            ),
            massive_crypto_websocket: TypedConnectionCollection::new(
                &mut self.massive_crypto_websocket_connections,
                MassiveCryptoWebSocketConnection::new,
                true,
            ),
            hyperliquid_websocket: TypedConnectionCollection::new(
                &mut self.hyperliquid_websocket_connections,
                HyperliquidWebSocketConnection::new,
                true,
            ),
            okx_public_rest: TypedConnectionCollection::new(
                &mut self.okx_public_rest_connections,
                OkxPublicRestConnection::new,
                false,
            ),
            okx_public_websocket: TypedConnectionCollection::new(
                &mut self.okx_public_websocket_connections,
                OkxPublicWebSocketConnection::new,
                true,
            ),
            okx_private_rest: TypedConnectionCollection::new(
                &mut self.okx_private_rest_connections,
                OkxPrivateRestConnection::new,
                false,
            ),
            okx_private_websocket: TypedConnectionCollection::new(
                &mut self.okx_private_websocket_connections,
                OkxPrivateWebSocketConnection::new,
                true,
            ),
        }
    }

    /// Borrows the process-owned output pipes. Callers can declare and publish
    /// through these capabilities but cannot take ownership of a transport.
    pub fn outputs(&mut self) -> crate::OutputCollections<'_> {
        crate::OutputCollections::new(&mut self.aeron_publishers, &mut self.indexed_writers)
    }

    pub(crate) fn stop_outputs(&mut self) {
        self.aeron_publishers.clear();
        self.indexed_writers.clear();
    }

    pub fn install_account_contract(
        &mut self,
        key: impl Into<String>,
        client: AccountClient,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self.account_clients.ensure_with_entry(key, 1, || client)?;
        client.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_account_connection(
        &mut self,
        key: impl Into<String>,
        control_socket: impl Into<PathBuf>,
        view_root: Option<PathBuf>,
    ) -> Result<(), crate::ResourceError> {
        let connection = kairos_account_contract::AccountConnection::control_only(control_socket);
        let connection = if let Some(view_root) = view_root {
            connection.with_view_root(view_root)
        } else {
            connection
        };
        self.install_account_contract(key, AccountClient::connect(connection))
    }

    pub fn install_capital_contract(
        &mut self,
        key: impl Into<String>,
        client: CapitalClient,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self.capital_clients.ensure_with_entry(key, 1, || client)?;
        client.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_capital_connection(
        &mut self,
        key: impl Into<String>,
        control_socket: impl Into<PathBuf>,
        view_root: Option<PathBuf>,
    ) -> Result<(), crate::ResourceError> {
        let connection = kairos_capital_contract::CapitalConnection::control_only(control_socket);
        let connection = if let Some(view_root) = view_root {
            connection.with_view_root(view_root)
        } else {
            connection
        };
        self.install_capital_contract(key, CapitalClient::connect(connection))
    }

    pub fn install_execution_contract(
        &mut self,
        key: impl Into<String>,
        client: ExecutionClient,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self
            .execution_clients
            .ensure_with_entry(key, 1, || client)?;
        client.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_execution_connection(
        &mut self,
        key: impl Into<String>,
        control_socket: impl Into<PathBuf>,
        view_root: Option<PathBuf>,
    ) -> Result<(), crate::ResourceError> {
        let connection =
            kairos_execution_contract::ExecutionConnection::control_only(control_socket);
        let connection = if let Some(view_root) = view_root {
            connection.with_view_root(view_root)
        } else {
            connection
        };
        self.install_execution_contract(key, ExecutionClient::connect(connection))
    }

    pub fn install_market_contract(
        &mut self,
        key: impl Into<String>,
        client: MarketClient,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self.market_clients.ensure_with_entry(key, 1, || client)?;
        client.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_market_connection(
        &mut self,
        key: impl Into<String>,
        control_socket: impl Into<PathBuf>,
        view_root: Option<PathBuf>,
    ) -> Result<(), crate::ResourceError> {
        let connection = kairos_market_contract::MarketConnection::control_only(control_socket);
        let connection = if let Some(view_root) = view_root {
            connection.with_view_root(view_root)
        } else {
            connection
        };
        self.install_market_contract(key, MarketClient::connect(connection))
    }

    pub fn install_reference_contract(
        &mut self,
        key: impl Into<String>,
        client: ReferenceClient,
        stream: ReferenceEventStream,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self
            .reference_clients
            .ensure_with_entry(key.clone(), 1, || client)?;
        client.set_state(ResourceState::Ready);
        let (_, stream) = self
            .reference_event_streams
            .ensure_with_entry(key, 1, || stream)?;
        stream.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_reference_connection(
        &mut self,
        key: impl Into<String>,
        connection: kairos_reference_contract::ReferenceConnection,
        event_capacity: usize,
    ) -> Result<(), String> {
        let client = ReferenceClient::connect(connection);
        let stream = client
            .events(event_capacity)
            .map_err(|error| error.to_string())?;
        self.install_reference_contract(key, client, stream)
            .map_err(|error| error.to_string())
    }

    pub(crate) fn reference_client_mut(&mut self, key: &str) -> Option<&mut ReferenceClient> {
        self.reference_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn reference_client(&self, key: &str) -> Option<ReferenceClient> {
        self.reference_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
    }

    pub fn install_risk_contract(
        &mut self,
        key: impl Into<String>,
        client: RiskClient,
    ) -> Result<(), crate::ResourceError> {
        let key = key.into();
        let (_, client) = self.risk_clients.ensure_with_entry(key, 1, || client)?;
        client.set_state(ResourceState::Ready);
        Ok(())
    }

    pub fn install_risk_connection(
        &mut self,
        key: impl Into<String>,
        control_socket: impl Into<PathBuf>,
        view_root: Option<PathBuf>,
    ) -> Result<(), String> {
        let connection = kairos_risk_contract::RiskConnection::control_only(control_socket);
        let connection = if let Some(view_root) = view_root {
            connection.with_view_root(view_root)
        } else {
            connection
        };
        let client = RiskClient::connect(connection).map_err(|error| error.to_string())?;
        self.install_risk_contract(key, client)
            .map_err(|error| error.to_string())
    }

    pub(crate) fn account_client_mut(&mut self, key: &str) -> Option<&mut AccountClient> {
        self.account_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn account_client(&self, key: &str) -> Option<AccountClient> {
        self.account_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
    }

    pub(crate) fn capital_client_mut(&mut self, key: &str) -> Option<&mut CapitalClient> {
        self.capital_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn capital_client(&self, key: &str) -> Option<CapitalClient> {
        self.capital_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
    }

    pub(crate) fn execution_client_mut(&mut self, key: &str) -> Option<&mut ExecutionClient> {
        self.execution_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn execution_client(&self, key: &str) -> Option<ExecutionClient> {
        self.execution_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
    }

    pub(crate) fn market_client_mut(&mut self, key: &str) -> Option<&mut MarketClient> {
        self.market_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn market_client(&self, key: &str) -> Option<MarketClient> {
        self.market_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
    }

    pub(crate) fn risk_client_mut(&mut self, key: &str) -> Option<&mut RiskClient> {
        self.risk_clients
            .get_mut(&key.to_owned())
            .map(|managed| managed.client_mut())
    }

    pub fn risk_client(&self, key: &str) -> Option<RiskClient> {
        self.risk_clients
            .get(&key.to_owned())
            .map(|managed| managed.client().clone())
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

    pub(crate) fn startup_status(&self, state: &ConnectionDriverState) -> Result<bool, String> {
        let mut pending = false;
        macro_rules! inspect {
            ($field:ident) => {
                for (key, managed) in self.$field.iter() {
                    let source = format!("lifecycle:{}:{key}", stringify!($field));
                    match managed.state() {
                        ResourceState::Ready => {}
                        ResourceState::Created | ResourceState::Starting => pending = true,
                        ResourceState::Degraded => {
                            if managed.policy().required {
                                if state.lifecycle_retry_at.contains_key(&source) {
                                    pending = true;
                                } else {
                                    return Err(format!(
                                        "required connection `{key}` is degraded without a recovery attempt"
                                    ));
                                }
                            }
                        }
                        ResourceState::Failed
                        | ResourceState::Stopping
                        | ResourceState::Retiring
                        | ResourceState::Stopped => {
                            if managed.policy().required {
                                return Err(format!(
                                    "required connection `{key}` failed during startup"
                                ));
                            }
                        }
                    }
                }
            };
        }

        inspect!(binance_spot_websocket_connections);
        inspect!(binance_spot_user_websocket_connections);
        inspect!(binance_margin_websocket_connections);
        inspect!(binance_margin_user_websocket_connections);
        inspect!(binance_usdm_websocket_connections);
        inspect!(binance_usdm_user_websocket_connections);
        inspect!(binance_coinm_websocket_connections);
        inspect!(binance_coinm_user_websocket_connections);
        inspect!(binance_options_websocket_connections);
        inspect!(binance_options_user_websocket_connections);
        inspect!(binance_stocks_websocket_connections);
        inspect!(binance_stocks_user_websocket_connections);
        inspect!(okx_public_websocket_connections);
        inspect!(okx_private_websocket_connections);
        inspect!(hyperliquid_websocket_connections);
        inspect!(ibkr_account_query_connections);
        inspect!(ibkr_account_stream_connections);
        inspect!(ibkr_order_connections);
        inspect!(ibkr_execution_stream_connections);
        inspect!(ibkr_market_data_connections);
        inspect!(massive_stocks_websocket_connections);
        inspect!(massive_options_websocket_connections);
        inspect!(massive_futures_websocket_connections);
        inspect!(massive_indices_websocket_connections);
        inspect!(massive_forex_websocket_connections);
        inspect!(massive_crypto_websocket_connections);
        Ok(!pending)
    }

    /// Disconnects every long-lived connection before the System is dropped.
    /// Entries remain in their managed collections so shutdown never transfers
    /// ownership to a task or business module.
    pub(crate) async fn stop_connections(&mut self, state: &mut ConnectionDriverState) {
        macro_rules! request_stop {
            ($field:ident) => {{
                for (_, managed) in self.$field.iter_mut() {
                    if managed.state() == ResourceState::Created {
                        managed.set_state(ResourceState::Stopped);
                    } else if managed.state() != ResourceState::Stopped {
                        managed.set_state(ResourceState::Stopping);
                    }
                }
            }};
        }
        request_stop!(binance_spot_websocket_connections);
        request_stop!(binance_spot_user_websocket_connections);
        request_stop!(binance_margin_websocket_connections);
        request_stop!(binance_margin_user_websocket_connections);
        request_stop!(binance_usdm_websocket_connections);
        request_stop!(binance_usdm_user_websocket_connections);
        request_stop!(binance_coinm_websocket_connections);
        request_stop!(binance_coinm_user_websocket_connections);
        request_stop!(binance_options_websocket_connections);
        request_stop!(binance_options_user_websocket_connections);
        request_stop!(binance_stocks_websocket_connections);
        request_stop!(binance_stocks_user_websocket_connections);
        request_stop!(okx_public_websocket_connections);
        request_stop!(okx_private_websocket_connections);
        request_stop!(hyperliquid_websocket_connections);
        request_stop!(ibkr_account_query_connections);
        request_stop!(ibkr_account_stream_connections);
        request_stop!(ibkr_order_connections);
        request_stop!(ibkr_execution_stream_connections);
        request_stop!(ibkr_market_data_connections);
        request_stop!(massive_stocks_websocket_connections);
        request_stop!(massive_options_websocket_connections);
        request_stop!(massive_futures_websocket_connections);
        request_stop!(massive_indices_websocket_connections);
        request_stop!(massive_forex_websocket_connections);
        request_stop!(massive_crypto_websocket_connections);

        std::future::poll_fn(|cx| {
            self.poll_all_connection_lifecycle(cx, Instant::now(), state);
            let mut complete = true;
            macro_rules! check_stopped {
                ($field:ident) => {
                    complete &= self.$field.iter().all(|(_, managed)| {
                        matches!(
                            managed.state(),
                            ResourceState::Stopped | ResourceState::Failed
                        )
                    });
                };
            }
            check_stopped!(binance_spot_websocket_connections);
            check_stopped!(binance_spot_user_websocket_connections);
            check_stopped!(binance_margin_websocket_connections);
            check_stopped!(binance_margin_user_websocket_connections);
            check_stopped!(binance_usdm_websocket_connections);
            check_stopped!(binance_usdm_user_websocket_connections);
            check_stopped!(binance_coinm_websocket_connections);
            check_stopped!(binance_coinm_user_websocket_connections);
            check_stopped!(binance_options_websocket_connections);
            check_stopped!(binance_options_user_websocket_connections);
            check_stopped!(binance_stocks_websocket_connections);
            check_stopped!(binance_stocks_user_websocket_connections);
            check_stopped!(okx_public_websocket_connections);
            check_stopped!(okx_private_websocket_connections);
            check_stopped!(hyperliquid_websocket_connections);
            check_stopped!(ibkr_account_query_connections);
            check_stopped!(ibkr_account_stream_connections);
            check_stopped!(ibkr_order_connections);
            check_stopped!(ibkr_execution_stream_connections);
            check_stopped!(ibkr_market_data_connections);
            check_stopped!(massive_stocks_websocket_connections);
            check_stopped!(massive_options_websocket_connections);
            check_stopped!(massive_futures_websocket_connections);
            check_stopped!(massive_indices_websocket_connections);
            check_stopped!(massive_forex_websocket_connections);
            check_stopped!(massive_crypto_websocket_connections);
            if complete {
                Poll::Ready(())
            } else {
                Poll::Pending
            }
        })
        .await;
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

        self.poll_all_connection_lifecycle(cx, Instant::now(), state);

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
                            let event = ($map)(event);
                            if participant_event_identity(&event).is_some_and(
                                |(participant, connection_key)| {
                                    participant != &descriptor.participant
                                        || connection_key != &descriptor.connection_key
                                },
                            ) {
                                managed.set_state(ResourceState::Failed);
                                let error = format!(
                                    "provider event identity does not match managed descriptor {}",
                                    descriptor.connection_key
                                );
                                state.push(
                                    format!("connection-state:{}:{key}", $family),
                                    ConnectionDriverOutput::System(
                                        SystemEvent::ConnectionStateChanged {
                                            connection: ManagedConnectionIdentity {
                                                descriptor,
                                                generation,
                                            },
                                            state: ResourceState::Failed,
                                            error: Some(error),
                                        },
                                    ),
                                );
                                continue;
                            }
                            state.push(
                                source,
                                ConnectionDriverOutput::Integration(IntegrationEvent {
                                    identity: ManagedConnectionIdentity {
                                        descriptor,
                                        generation,
                                    },
                                    event,
                                }),
                            );
                        },
                        Poll::Ready(Err(error)) => {
                            let recoverable = is_recoverable_connection_error(&error);
                            let retry_scheduled = recoverable
                                && state.schedule_retry(
                                    &format!("lifecycle:{}:{key}", stringify!($field)),
                                    Instant::now(),
                                    managed.policy().recovery,
                                );
                            managed.set_state(
                                if is_permanent_connection_error(&error)
                                    || (recoverable && !retry_scheduled)
                                {
                                    ResourceState::Failed
                                } else {
                                    ResourceState::Degraded
                                },
                            );
                            state.push(
                                format!("connection-state:{}:{key}", $family),
                                ConnectionDriverOutput::System(
                                    SystemEvent::ConnectionStateChanged {
                                        connection: ManagedConnectionIdentity {
                                            descriptor,
                                            generation,
                                        },
                                        state: managed.state(),
                                        error: Some(error.to_string()),
                                    },
                                ),
                            );
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        },
                        Poll::Pending => {},
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
                19 => poll_family!(
                    massive_futures_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.futures"
                ),
                20 => poll_family!(
                    massive_indices_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.indices"
                ),
                21 => poll_family!(
                    massive_forex_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.forex"
                ),
                22 => poll_family!(
                    massive_crypto_websocket_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "massive.crypto"
                ),
                23 => poll_family!(
                    ibkr_market_data_connections,
                    kairos_integration::MarketDataStream,
                    kairos_integration::ExternalParticipantEvent::Market,
                    "ibkr.market"
                ),
                _ => unreachable!(),
            }
        }
        state.family_cursor = (state.family_cursor + 1) % CONNECTION_EVENT_FAMILIES;

        self.poll_all_connection_maintenance(cx, Instant::now(), state);
        self.poll_all_contract_events(cx, state);
        state.pop().map_or(Poll::Pending, Poll::Ready)
    }

    fn poll_all_connection_lifecycle(
        &mut self,
        cx: &mut Context<'_>,
        now: Instant,
        state: &mut ConnectionDriverState,
    ) {
        macro_rules! poll_lifecycle {
            ($field:ident) => {{
                let mut keys = self
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    let source = format!("integration:{key}");
                    let lifecycle_source = format!("lifecycle:{}:{key}", stringify!($field));
                    let Some(managed) = self.$field.get_mut(&key) else {
                        state.clear_retry(&lifecycle_source);
                        continue;
                    };
                    if !managed.lifecycle_in_progress() {
                        let operation = match managed.state() {
                            ResourceState::Created => Some(ManagedLifecycleOperation::Connect),
                            ResourceState::Retiring => Some(ManagedLifecycleOperation::Disconnect),
                            ResourceState::Stopping => Some(ManagedLifecycleOperation::Disconnect),
                            ResourceState::Degraded | ResourceState::Failed
                                if state.retry_due(&lifecycle_source, now) =>
                            {
                                Some(ManagedLifecycleOperation::Reconnect)
                            },
                            _ => None,
                        };
                        if let Some(operation) = operation {
                            if !matches!(operation, ManagedLifecycleOperation::Disconnect) {
                                managed.set_state(ResourceState::Starting);
                            }
                            managed.begin_lifecycle(operation);
                        }
                    }
                    let Poll::Ready((operation, result)) = managed.poll_lifecycle(cx) else {
                        continue;
                    };
                    let identity = ManagedConnectionIdentity {
                        descriptor: managed.connection().descriptor().clone(),
                        generation: managed.generation(),
                    };
                    match result {
                        Ok(()) => {
                            if matches!(
                                managed.state(),
                                ResourceState::Retiring | ResourceState::Stopping
                            ) && !matches!(operation, ManagedLifecycleOperation::Disconnect)
                            {
                                managed.begin_lifecycle(ManagedLifecycleOperation::Disconnect);
                                cx.waker().wake_by_ref();
                                continue;
                            }
                            let retiring = managed.state() == ResourceState::Retiring;
                            state.clear_retry(&lifecycle_source);
                            managed.set_state(match operation {
                                ManagedLifecycleOperation::Connect
                                | ManagedLifecycleOperation::Reconnect => ResourceState::Ready,
                                ManagedLifecycleOperation::Disconnect => ResourceState::Stopped,
                            });
                            state.push(
                                format!("connection-state:{}:{key}", stringify!($field)),
                                ConnectionDriverOutput::System(
                                    SystemEvent::ConnectionStateChanged {
                                        connection: identity,
                                        state: managed.state(),
                                        error: None,
                                    },
                                ),
                            );
                            if matches!(operation, ManagedLifecycleOperation::Disconnect)
                                && retiring
                            {
                                state.push(
                                    format!("lifecycle-stop:{}:{key}", stringify!($field)),
                                    ConnectionDriverOutput::LifecycleStopped {
                                        collection: stringify!($field),
                                        key,
                                        result: Ok(()),
                                    },
                                );
                            } else if !matches!(operation, ManagedLifecycleOperation::Disconnect) {
                                state.push(
                                    source.clone(),
                                    ConnectionDriverOutput::System(SystemEvent::SourceReady {
                                        source,
                                    }),
                                );
                            }
                        },
                        Err(error) => {
                            if matches!(
                                managed.state(),
                                ResourceState::Retiring | ResourceState::Stopping
                            ) && !matches!(operation, ManagedLifecycleOperation::Disconnect)
                            {
                                managed.begin_lifecycle(ManagedLifecycleOperation::Disconnect);
                                cx.waker().wake_by_ref();
                                continue;
                            }
                            if matches!(operation, ManagedLifecycleOperation::Disconnect) {
                                let retiring = managed.state() == ResourceState::Retiring;
                                state.clear_retry(&lifecycle_source);
                                managed.set_state(ResourceState::Failed);
                                state.push(
                                    format!("connection-state:{}:{key}", stringify!($field)),
                                    ConnectionDriverOutput::System(
                                        SystemEvent::ConnectionStateChanged {
                                            connection: identity,
                                            state: ResourceState::Failed,
                                            error: Some(error.to_string()),
                                        },
                                    ),
                                );
                                if retiring {
                                    state.push(
                                        format!("lifecycle-stop:{}:{key}", stringify!($field)),
                                        ConnectionDriverOutput::LifecycleStopped {
                                            collection: stringify!($field),
                                            key,
                                            result: Err(error.to_string()),
                                        },
                                    );
                                }
                                continue;
                            }
                            let recoverable = is_recoverable_connection_error(&error);
                            let retry_scheduled = recoverable
                                && state.schedule_retry(
                                    &lifecycle_source,
                                    now,
                                    managed.policy().recovery,
                                );
                            let next_state = if is_permanent_connection_error(&error)
                                || !recoverable
                                || !retry_scheduled
                            {
                                state.clear_retry(&lifecycle_source);
                                ResourceState::Failed
                            } else {
                                ResourceState::Degraded
                            };
                            managed.set_state(next_state);
                            state.push(
                                format!("connection-state:{}:{key}", stringify!($field)),
                                ConnectionDriverOutput::System(
                                    SystemEvent::ConnectionStateChanged {
                                        connection: identity,
                                        state: managed.state(),
                                        error: Some(error.to_string()),
                                    },
                                ),
                            );
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        },
                    }
                }
            }};
        }

        poll_lifecycle!(binance_spot_websocket_connections);
        poll_lifecycle!(binance_spot_user_websocket_connections);
        poll_lifecycle!(binance_margin_websocket_connections);
        poll_lifecycle!(binance_margin_user_websocket_connections);
        poll_lifecycle!(binance_usdm_websocket_connections);
        poll_lifecycle!(binance_usdm_user_websocket_connections);
        poll_lifecycle!(binance_coinm_websocket_connections);
        poll_lifecycle!(binance_coinm_user_websocket_connections);
        poll_lifecycle!(binance_options_websocket_connections);
        poll_lifecycle!(binance_options_user_websocket_connections);
        poll_lifecycle!(binance_stocks_websocket_connections);
        poll_lifecycle!(binance_stocks_user_websocket_connections);
        poll_lifecycle!(okx_public_websocket_connections);
        poll_lifecycle!(okx_private_websocket_connections);
        poll_lifecycle!(hyperliquid_websocket_connections);
        poll_lifecycle!(ibkr_account_query_connections);
        poll_lifecycle!(ibkr_account_stream_connections);
        poll_lifecycle!(ibkr_order_connections);
        poll_lifecycle!(ibkr_execution_stream_connections);
        poll_lifecycle!(ibkr_market_data_connections);
        poll_lifecycle!(massive_stocks_websocket_connections);
        poll_lifecycle!(massive_options_websocket_connections);
        poll_lifecycle!(massive_futures_websocket_connections);
        poll_lifecycle!(massive_indices_websocket_connections);
        poll_lifecycle!(massive_forex_websocket_connections);
        poll_lifecycle!(massive_crypto_websocket_connections);
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
                        },
                        Poll::Ready(None) => managed.set_state(ResourceState::Stopped),
                        Poll::Pending => {},
                    }
                }
            }};
        }

        const FAMILIES: usize = 6;
        for offset in 0..FAMILIES {
            match (state.contract_family_cursor + offset) % FAMILIES {
                0 => poll_contract!(account_event_streams, Account, "account"),
                1 => poll_contract!(capital_event_streams, Capital, "capital"),
                2 => poll_contract!(execution_event_streams, Execution, "execution"),
                3 => poll_contract!(market_event_streams, Market, "market"),
                4 => poll_contract!(reference_event_streams, Reference, "reference"),
                5 => poll_contract!(risk_event_streams, Risk, "risk"),
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
                    let maintenance_continuing = state.maintenance_in_progress.contains(&source);
                    if managed.state() != ResourceState::Ready
                        && !(managed.state() == ResourceState::Degraded && maintenance_continuing)
                    {
                        continue;
                    }
                    let due = maintenance_continuing
                        || kairos_integration::ConnectionMaintenance::next_maintenance_at(
                            managed.connection(),
                        )
                        .is_some_and(|deadline| deadline <= now);
                    if !due {
                        continue;
                    }
                    let identity = ManagedConnectionIdentity {
                        descriptor: managed.connection().descriptor().clone(),
                        generation: managed.generation(),
                    };
                    match kairos_integration::ConnectionMaintenance::poll_maintenance(
                        managed.connection_mut(),
                        cx,
                        now,
                    ) {
                        Poll::Pending => {
                            state.maintenance_in_progress.insert(source);
                        },
                        Poll::Ready(Ok(kairos_integration::MaintenanceOutcome::Healthy))
                        | Poll::Ready(Ok(kairos_integration::MaintenanceOutcome::Progressed)) => {
                            state.maintenance_in_progress.remove(&source);
                        },
                        Poll::Ready(Ok(
                            kairos_integration::MaintenanceOutcome::ReconnectRequired { reason },
                        )) => {
                            state.maintenance_in_progress.remove(&source);
                            let retry_scheduled = state.schedule_retry(
                                &format!("lifecycle:{}:{key}", stringify!($field)),
                                now,
                                managed.policy().recovery,
                            );
                            managed.set_state(if retry_scheduled {
                                ResourceState::Degraded
                            } else {
                                ResourceState::Failed
                            });
                            state.push(
                                format!("connection-state:{}:{key}", $family),
                                ConnectionDriverOutput::System(
                                    SystemEvent::ConnectionStateChanged {
                                        connection: identity,
                                        state: managed.state(),
                                        error: Some(reason.clone()),
                                    },
                                ),
                            );
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: reason,
                                }),
                            );
                        },
                        Poll::Ready(Err(error)) => {
                            state.maintenance_in_progress.remove(&source);
                            let recoverable = is_recoverable_connection_error(&error);
                            let retry_scheduled = recoverable
                                && state.schedule_retry(
                                    &format!("lifecycle:{}:{key}", stringify!($field)),
                                    now,
                                    managed.policy().recovery,
                                );
                            managed.set_state(
                                if is_permanent_connection_error(&error)
                                    || (recoverable && !retry_scheduled)
                                {
                                    ResourceState::Failed
                                } else {
                                    ResourceState::Degraded
                                },
                            );
                            state.push(
                                format!("connection-state:{}:{key}", $family),
                                ConnectionDriverOutput::System(
                                    SystemEvent::ConnectionStateChanged {
                                        connection: identity,
                                        state: managed.state(),
                                        error: Some(error.to_string()),
                                    },
                                ),
                            );
                            state.push(
                                source.clone(),
                                ConnectionDriverOutput::System(SystemEvent::SourceFailed {
                                    source,
                                    error: error.to_string(),
                                }),
                            );
                        },
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
        poll_maintenance!(massive_futures_websocket_connections, "massive.futures");
        poll_maintenance!(massive_indices_websocket_connections, "massive.indices");
        poll_maintenance!(massive_forex_websocket_connections, "massive.forex");
        poll_maintenance!(massive_crypto_websocket_connections, "massive.crypto");
    }

    pub(crate) fn next_wakeup_deadline(
        &self,
        connection_driver: &ConnectionDriverState,
    ) -> Option<Instant> {
        let mut deadline = if self.pending_system_events.is_empty() {
            self.timers
                .values()
                .map(|timer| timer.next)
                .chain(connection_driver.lifecycle_retry_at.values().copied())
                .min()
        } else {
            Some(Instant::now())
        };
        macro_rules! visit {
            ($field:ident, $family:literal) => {
                for (key, managed) in self.$field.iter() {
                    if managed.state() != ResourceState::Ready || managed.lifecycle_in_progress() {
                        continue;
                    }
                    let source = format!("maintenance:{}:{}", $family, key);
                    if let Some(candidate) = connection_driver.pending_maintenance_deadline(
                        &source,
                        kairos_integration::ConnectionMaintenance::next_maintenance_at(
                            managed.connection(),
                        ),
                    ) {
                        deadline =
                            Some(deadline.map_or(candidate, |value: Instant| value.min(candidate)));
                    }
                }
            };
        }
        visit!(binance_spot_websocket_connections, "binance.spot.market");
        visit!(binance_spot_user_websocket_connections, "binance.spot.user");
        visit!(
            binance_margin_websocket_connections,
            "binance.margin.market"
        );
        visit!(
            binance_margin_user_websocket_connections,
            "binance.margin.user"
        );
        visit!(binance_usdm_websocket_connections, "binance.usdm.market");
        visit!(binance_usdm_user_websocket_connections, "binance.usdm.user");
        visit!(binance_coinm_websocket_connections, "binance.coinm.market");
        visit!(
            binance_coinm_user_websocket_connections,
            "binance.coinm.user"
        );
        visit!(
            binance_options_websocket_connections,
            "binance.options.market"
        );
        visit!(
            binance_options_user_websocket_connections,
            "binance.options.user"
        );
        visit!(
            binance_stocks_websocket_connections,
            "binance.stocks.market"
        );
        visit!(
            binance_stocks_user_websocket_connections,
            "binance.stocks.user"
        );
        visit!(okx_public_websocket_connections, "okx.public.market");
        visit!(okx_private_websocket_connections, "okx.private");
        visit!(hyperliquid_websocket_connections, "hyperliquid.websocket");
        visit!(ibkr_account_query_connections, "ibkr.account.query");
        visit!(ibkr_account_stream_connections, "ibkr.account");
        visit!(ibkr_order_connections, "ibkr.order");
        visit!(ibkr_execution_stream_connections, "ibkr.execution");
        visit!(ibkr_market_data_connections, "ibkr.market");
        visit!(massive_stocks_websocket_connections, "massive.stocks");
        visit!(massive_options_websocket_connections, "massive.options");
        visit!(massive_futures_websocket_connections, "massive.futures");
        visit!(massive_indices_websocket_connections, "massive.indices");
        visit!(massive_forex_websocket_connections, "massive.forex");
        visit!(massive_crypto_websocket_connections, "massive.crypto");
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
    use kairos_primitives::integration::ParticipantSymbol;
    use kairos_primitives::time::UnixNanos;

    use super::*;

    #[test]
    fn lifecycle_retry_only_becomes_due_after_it_is_scheduled() {
        let mut state = ConnectionDriverState::new();
        let now = Instant::now();

        assert!(!state.retry_due("lifecycle:test", now));
        state.schedule_retry("lifecycle:test", now, crate::RecoveryPolicy::default());
        assert!(!state.retry_due("lifecycle:test", now));
        assert!(state.retry_due("lifecycle:test", now + Duration::from_secs(1)));

        state.schedule_retry(
            "lifecycle:test",
            now + Duration::from_secs(1),
            crate::RecoveryPolicy::default(),
        );
        assert!(!state.retry_due("lifecycle:test", now + Duration::from_secs(2)));
        assert!(state.retry_due("lifecycle:test", now + Duration::from_secs(3)));

        state.clear_retry("lifecycle:test");
        assert!(!state.retry_due("lifecycle:test", now + Duration::from_secs(60)));
    }

    #[test]
    fn authentication_failure_is_permanent_and_transport_failure_is_recoverable() {
        let authentication = kairos_integration::IntegrationError::Authentication("denied".into());
        assert!(is_permanent_connection_error(&authentication));
        assert!(!is_recoverable_connection_error(&authentication));

        let transport = kairos_integration::IntegrationError::Transport("closed".into());
        assert!(!is_permanent_connection_error(&transport));
        assert!(is_recoverable_connection_error(&transport));
    }

    #[test]
    fn lifecycle_retry_honors_custom_backoff_and_attempt_limit() {
        let mut state = ConnectionDriverState::new();
        let now = Instant::now();
        let policy = crate::RecoveryPolicy {
            initial_backoff: Duration::from_millis(10),
            maximum_backoff: Duration::from_millis(15),
            maximum_attempts: Some(2),
        };

        assert!(state.schedule_retry("lifecycle:test", now, policy));
        assert!(!state.retry_due("lifecycle:test", now + Duration::from_millis(9)));
        assert!(state.retry_due("lifecycle:test", now + Duration::from_millis(10)));
        assert!(state.schedule_retry("lifecycle:test", now, policy));
        assert!(!state.retry_due("lifecycle:test", now + Duration::from_millis(14)));
        assert!(state.retry_due("lifecycle:test", now + Duration::from_millis(15)));
        assert!(!state.schedule_retry("lifecycle:test", now, policy));
        assert!(!state.retry_due("lifecycle:test", now + Duration::from_secs(1)));
    }

    #[test]
    fn context_access_waits_for_managed_connections_but_not_bounded_clients() {
        struct FakeConnection;

        fn create_fake(
            _: ConnectionKey,
            _: (),
        ) -> Result<FakeConnection, kairos_integration::IntegrationError> {
            Ok(FakeConnection)
        }

        let key = ConnectionKey::new("readiness-test").unwrap();
        let mut managed = ManagedConnections::new();
        let mut view = TypedConnectionCollection::new(&mut managed, create_fake, true);
        view.create(key.clone(), ()).unwrap();
        assert!(matches!(
            view.get(&key),
            Err(ConnectionAccessError::NotReady(value)) if value == key
        ));
        assert!(
            matches!(view.get_shared(&key), Err(ConnectionAccessError::NotReady(value)) if value == key)
        );
        drop(view);
        managed
            .get_mut(&key.to_string())
            .unwrap()
            .set_state(ResourceState::Ready);
        assert!(
            TypedConnectionCollection::new(&mut managed, create_fake, true)
                .get(&key)
                .is_ok()
        );

        let mut bounded = ManagedConnections::new();
        let mut view = TypedConnectionCollection::new(&mut bounded, create_fake, false);
        view.create(key.clone(), ()).unwrap();
        assert!(view.get(&key).is_ok());
        let first = view.get_shared(&key).unwrap();
        let second = view.get_shared(&key).unwrap();
        assert!(std::ptr::eq(first, second));
        drop(view);
        for state in [
            ResourceState::Starting,
            ResourceState::Failed,
            ResourceState::Stopping,
            ResourceState::Stopped,
            ResourceState::Retiring,
        ] {
            bounded.get_mut(&key.to_string()).unwrap().set_state(state);
            let mut view = TypedConnectionCollection::new(&mut bounded, create_fake, false);
            if state == ResourceState::Retiring {
                assert!(matches!(
                    view.get_shared(&key),
                    Err(ConnectionAccessError::Retiring(_))
                ));
                assert!(matches!(
                    view.get(&key),
                    Err(ConnectionAccessError::Retiring(_))
                ));
            } else {
                assert!(matches!(
                    view.get_shared(&key),
                    Err(ConnectionAccessError::NotReady(_))
                ));
                assert!(matches!(
                    view.get(&key),
                    Err(ConnectionAccessError::NotReady(_))
                ));
            }
        }
    }

    #[test]
    fn maintenance_in_progress_does_not_reuse_an_expired_timer_deadline() {
        let mut state = ConnectionDriverState::new();
        let source = "maintenance:test:key";
        let expired = Instant::now() - Duration::from_secs(1);

        assert_eq!(
            state.pending_maintenance_deadline(source, Some(expired)),
            Some(expired)
        );
        state.maintenance_in_progress.insert(source.into());
        assert_eq!(
            state.pending_maintenance_deadline(source, Some(expired)),
            None
        );
    }

    #[test]
    fn retiring_identity_purges_only_its_queued_generation() {
        fn market_event() -> kairos_integration::MarketEvent {
            kairos_integration::MarketEvent {
                symbol: ParticipantSymbol::new("BTC-USDT").unwrap(),
                kind: kairos_integration::MarketEventKind::Trade,
                price: None,
                quantity: None,
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: UnixNanos::from(1),
                venue: Default::default(),
            }
        }

        let descriptor = kairos_integration::ConnectionDescriptor::new(
            "shared-key",
            kairos_integration::ParticipantRef::new(
                kairos_integration::ParticipantKind::Exchange,
                "test",
            )
            .unwrap(),
            "market.websocket",
        )
        .unwrap();
        let old = ManagedConnectionIdentity {
            descriptor: descriptor.clone(),
            generation: 1,
        };
        let current = ManagedConnectionIdentity {
            descriptor,
            generation: 2,
        };
        let mut state = ConnectionDriverState::new();
        for (source, identity) in [("old", old.clone()), ("current", current.clone())] {
            state.push(
                source.into(),
                ConnectionDriverOutput::Integration(IntegrationEvent {
                    identity,
                    event: kairos_integration::ExternalParticipantEvent::Market(market_event()),
                }),
            );
        }

        state.purge_integration_identity(&old);

        assert_eq!(state.ready.len(), 1);
        assert!(matches!(
            state.pop(),
            Some(ConnectionDriverOutput::Integration(IntegrationEvent { identity, .. }))
                if identity == current
        ));
    }

    #[test]
    fn ready_scheduler_keeps_one_fair_bounded_slot_per_source() {
        let mut state = ConnectionDriverState::new();
        state.push(
            "source-a".into(),
            ConnectionDriverOutput::System(SystemEvent::SourceReady {
                source: "a-first".into(),
            }),
        );
        state.push(
            "source-a".into(),
            ConnectionDriverOutput::System(SystemEvent::SourceReady {
                source: "a-duplicate".into(),
            }),
        );
        state.push(
            "source-b".into(),
            ConnectionDriverOutput::System(SystemEvent::SourceReady {
                source: "b-first".into(),
            }),
        );

        assert_eq!(state.ready.len(), 2);
        assert!(matches!(
            state.pop(),
            Some(ConnectionDriverOutput::System(SystemEvent::SourceReady { source }))
                if source == "a-first"
        ));
        state.push(
            "source-a".into(),
            ConnectionDriverOutput::System(SystemEvent::SourceReady {
                source: "a-second".into(),
            }),
        );
        assert!(matches!(
            state.pop(),
            Some(ConnectionDriverOutput::System(SystemEvent::SourceReady { source }))
                if source == "b-first"
        ));
        assert!(matches!(
            state.pop(),
            Some(ConnectionDriverOutput::System(SystemEvent::SourceReady { source }))
                if source == "a-second"
        ));
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

    #[test]
    fn binance_capital_transfer_is_part_of_the_typed_connection_universe() {
        let mut system = ConfluxSystem::new();
        let key = ConnectionKey::new("capital-main").unwrap();
        let mut segment_accounts = BTreeMap::new();
        segment_accounts.insert(
            kairos_primitives::account::SegmentKey::new("funding").unwrap(),
            kairos_integration::participants::binance::capital::BinanceTransferAccount::Funding,
        );
        segment_accounts.insert(
            kairos_primitives::account::SegmentKey::new("usd-m").unwrap(),
            kairos_integration::participants::binance::capital::BinanceTransferAccount::UsdMFutures,
        );
        system
            .connections()
            .binance_capital_rest
            .create(
                key.clone(),
                BinanceCapitalRestConfig {
                    rest: BinanceRestConfig {
                        environment: "test".into(),
                        endpoint: "https://api.binance.com".into(),
                        credential: None,
                    },
                    segment_accounts,
                },
            )
            .unwrap();

        assert_eq!(system.connections().binance_capital_rest.keys(), vec![key]);
    }

    #[test]
    fn binance_subaccount_transfer_is_part_of_the_typed_connection_universe() {
        let mut system = ConfluxSystem::new();
        let key = ConnectionKey::new("capital-subaccounts").unwrap();
        let master_account_id = kairos_primitives::account::AccountId::new("master").unwrap();
        let subaccount_id = kairos_primitives::account::AccountId::new("subaccount-a").unwrap();
        let spot = kairos_primitives::account::SegmentKey::new("spot").unwrap();
        let usdm = kairos_primitives::account::SegmentKey::new("usd-m").unwrap();
        let mut accounts = BTreeMap::new();
        accounts.insert(
            master_account_id.clone(),
            kairos_integration::participants::binance::capital::BinanceSubAccountIdentity {
                email: None,
            },
        );
        accounts.insert(
            subaccount_id.clone(),
            kairos_integration::participants::binance::capital::BinanceSubAccountIdentity {
                email: Some("subaccount@example.com".into()),
            },
        );
        let mut segment_accounts = BTreeMap::new();
        segment_accounts.insert(
            (master_account_id.clone(), spot.clone()),
            kairos_integration::participants::binance::capital::BinanceTransferAccount::Spot,
        );
        segment_accounts.insert(
            (subaccount_id, usdm),
            kairos_integration::participants::binance::capital::BinanceTransferAccount::UsdMFutures,
        );

        system
            .connections()
            .binance_subaccount_capital_rest
            .create(
                key.clone(),
                BinanceSubAccountCapitalRestConfig {
                    rest: BinanceRestConfig {
                        environment: "test".into(),
                        endpoint: "https://api.binance.com".into(),
                        credential: Some(
                            kairos_integration::participants::binance::BinanceCredential {
                                principal_id: master_account_id.to_string(),
                                api_key: "test-key".into(),
                                secret: "test-secret".into(),
                            },
                        ),
                    },
                    master_account_id,
                    accounts,
                    segment_accounts,
                },
            )
            .unwrap();

        assert_eq!(
            system.connections().binance_subaccount_capital_rest.keys(),
            vec![key]
        );
    }
}
