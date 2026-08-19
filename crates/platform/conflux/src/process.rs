use std::collections::{HashMap, VecDeque};
use std::future::poll_fn;
use std::marker::PhantomData;
use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

use thiserror::Error;
use tokio::sync::{mpsc, oneshot, watch};

use crate::system::{ConnectionDriverOutput, ConnectionDriverState};
use crate::{
    ConfluxActor, ConfluxEvent, ConfluxSystem, ConnectionCreateError, ConnectionKey, Context,
    ContractEvent, ProcessPhase, ResourceState, RestRequestOf, RestResponseOf, ShutdownMode,
};

type ActorEvent<A> = ConfluxEvent<A, <A as ConfluxActor>::LocalEvent>;

pub(crate) struct EventEnvelope<A: ConfluxActor> {
    pub(crate) event: ActorEvent<A>,
    pub(crate) completed: oneshot::Sender<Option<RestResponseOf<A>>>,
}

struct RestEnvelope<A: ConfluxActor> {
    request: RestRequestOf<A>,
    completed: oneshot::Sender<Option<RestResponseOf<A>>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConfluxConfig {
    pub ingress_capacity: usize,
    pub shutdown_timeout: Duration,
}

impl Default for ConfluxConfig {
    fn default() -> Self {
        Self {
            ingress_capacity: 1_024,
            shutdown_timeout: Duration::from_secs(10),
        }
    }
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum BuildError {
    #[error("ingress capacity must be greater than zero")]
    ZeroIngressCapacity,
    #[error("shutdown timeout must be greater than zero")]
    ZeroShutdownTimeout,
}

#[derive(Debug, Error)]
pub enum ConnectionControlError {
    #[error(transparent)]
    Create(#[from] ConnectionCreateError),
    #[error("connection `{0}` does not exist")]
    NotFound(ConnectionKey),
    #[error("connection `{0}` is already retiring")]
    Retiring(ConnectionKey),
    #[error("Conflux connection-control channel is closed")]
    Closed,
    #[error("connections cannot be mutated while Conflux is {0:?}")]
    Unavailable(ProcessPhase),
    #[error("connection `{key}` failed to disconnect: {error}")]
    Disconnect { key: ConnectionKey, error: String },
}

macro_rules! for_each_handle_collection {
    ($macro:ident) => {
        $macro! {
            (binance_spot_rest, BinanceSpotRestHandle, crate::BinanceRestConfig, CreateBinanceSpotRest, RemoveBinanceSpotRest, binance_spot_rest, binance_spot_rest_connections, Rest),
            (binance_funding_rest, BinanceFundingRestHandle, crate::BinanceRestConfig, CreateBinanceFundingRest, RemoveBinanceFundingRest, binance_funding_rest, binance_funding_rest_connections, Rest),
            (binance_earn_rest, BinanceEarnRestHandle, crate::BinanceRestConfig, CreateBinanceEarnRest, RemoveBinanceEarnRest, binance_earn_rest, binance_earn_rest_connections, Rest),
            (binance_margin_rest, BinanceMarginRestHandle, crate::BinanceRestConfig, CreateBinanceMarginRest, RemoveBinanceMarginRest, binance_margin_rest, binance_margin_rest_connections, Rest),
            (binance_usdm_rest, BinanceUsdMRestHandle, crate::BinanceRestConfig, CreateBinanceUsdMRest, RemoveBinanceUsdMRest, binance_usdm_rest, binance_usdm_rest_connections, Rest),
            (binance_coinm_rest, BinanceCoinMRestHandle, crate::BinanceRestConfig, CreateBinanceCoinMRest, RemoveBinanceCoinMRest, binance_coinm_rest, binance_coinm_rest_connections, Rest),
            (binance_options_rest, BinanceOptionsRestHandle, crate::BinanceRestConfig, CreateBinanceOptionsRest, RemoveBinanceOptionsRest, binance_options_rest, binance_options_rest_connections, Rest),
            (binance_stocks_rest, BinanceStocksRestHandle, crate::BinanceRestConfig, CreateBinanceStocksRest, RemoveBinanceStocksRest, binance_stocks_rest, binance_stocks_rest_connections, Rest),
            (binance_spot_websocket, BinanceSpotWebSocketHandle, crate::BinanceWebSocketConfig, CreateBinanceSpotWebSocket, RemoveBinanceSpotWebSocket, binance_spot_websocket, binance_spot_websocket_connections, Stream),
            (binance_usdm_websocket, BinanceUsdMWebSocketHandle, crate::BinanceWebSocketConfig, CreateBinanceUsdMWebSocket, RemoveBinanceUsdMWebSocket, binance_usdm_websocket, binance_usdm_websocket_connections, Stream),
            (binance_coinm_websocket, BinanceCoinMWebSocketHandle, crate::BinanceWebSocketConfig, CreateBinanceCoinMWebSocket, RemoveBinanceCoinMWebSocket, binance_coinm_websocket, binance_coinm_websocket_connections, Stream),
            (binance_options_websocket, BinanceOptionsWebSocketHandle, crate::BinanceWebSocketConfig, CreateBinanceOptionsWebSocket, RemoveBinanceOptionsWebSocket, binance_options_websocket, binance_options_websocket_connections, Stream),
            (binance_stocks_websocket, BinanceStocksWebSocketHandle, crate::BinanceWebSocketConfig, CreateBinanceStocksWebSocket, RemoveBinanceStocksWebSocket, binance_stocks_websocket, binance_stocks_websocket_connections, Stream),
            (binance_spot_user_websocket, BinanceSpotUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceSpotUserWebSocket, RemoveBinanceSpotUserWebSocket, binance_spot_user_websocket, binance_spot_user_websocket_connections, Stream),
            (binance_margin_user_websocket, BinanceMarginUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceMarginUserWebSocket, RemoveBinanceMarginUserWebSocket, binance_margin_user_websocket, binance_margin_user_websocket_connections, Stream),
            (binance_usdm_user_websocket, BinanceUsdMUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceUsdMUserWebSocket, RemoveBinanceUsdMUserWebSocket, binance_usdm_user_websocket, binance_usdm_user_websocket_connections, Stream),
            (binance_coinm_user_websocket, BinanceCoinMUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceCoinMUserWebSocket, RemoveBinanceCoinMUserWebSocket, binance_coinm_user_websocket, binance_coinm_user_websocket_connections, Stream),
            (binance_options_user_websocket, BinanceOptionsUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceOptionsUserWebSocket, RemoveBinanceOptionsUserWebSocket, binance_options_user_websocket, binance_options_user_websocket_connections, Stream),
            (binance_stocks_user_websocket, BinanceStocksUserWebSocketHandle, crate::BinanceUserWebSocketConfig, CreateBinanceStocksUserWebSocket, RemoveBinanceStocksUserWebSocket, binance_stocks_user_websocket, binance_stocks_user_websocket_connections, Stream),
            (ibkr_account_query, IbkrAccountQueryHandle, crate::IbkrAccountQueryConfig, CreateIbkrAccountQuery, RemoveIbkrAccountQuery, ibkr_account_query, ibkr_account_query_connections, Stream),
            (ibkr_account_stream, IbkrAccountStreamHandle, crate::IbkrAccountStreamConfig, CreateIbkrAccountStream, RemoveIbkrAccountStream, ibkr_account_stream, ibkr_account_stream_connections, Stream),
            (ibkr_order, IbkrOrderHandle, crate::IbkrOrderConfig, CreateIbkrOrder, RemoveIbkrOrder, ibkr_order, ibkr_order_connections, Stream),
            (ibkr_execution_stream, IbkrExecutionStreamHandle, crate::IbkrExecutionStreamConfig, CreateIbkrExecutionStream, RemoveIbkrExecutionStream, ibkr_execution_stream, ibkr_execution_stream_connections, Stream),
            (ibkr_market_data, IbkrMarketDataHandle, crate::IbkrMarketDataConfig, CreateIbkrMarketData, RemoveIbkrMarketData, ibkr_market_data, ibkr_market_data_connections, Stream),
            (hyperliquid_info_rest, HyperliquidInfoRestHandle, crate::HyperliquidRestConfig, CreateHyperliquidInfoRest, RemoveHyperliquidInfoRest, hyperliquid_info_rest, hyperliquid_info_rest_connections, Rest),
            (hyperliquid_websocket, HyperliquidWebSocketHandle, crate::HyperliquidWebSocketConfig, CreateHyperliquidWebSocket, RemoveHyperliquidWebSocket, hyperliquid_websocket, hyperliquid_websocket_connections, Stream),
            (massive_rest, MassiveRestHandle, crate::MassiveRestConfig, CreateMassiveRest, RemoveMassiveRest, massive_rest, massive_rest_connections, Rest),
            (massive_stocks_websocket, MassiveStocksWebSocketHandle, crate::MassiveWebSocketConfig, CreateMassiveStocksWebSocket, RemoveMassiveStocksWebSocket, massive_stocks_websocket, massive_stocks_websocket_connections, Stream),
            (massive_options_websocket, MassiveOptionsWebSocketHandle, crate::MassiveWebSocketConfig, CreateMassiveOptionsWebSocket, RemoveMassiveOptionsWebSocket, massive_options_websocket, massive_options_websocket_connections, Stream),
            (okx_public_rest, OkxPublicRestHandle, crate::OkxRestConfig, CreateOkxPublicRest, RemoveOkxPublicRest, okx_public_rest, okx_public_rest_connections, Rest),
            (okx_public_websocket, OkxPublicWebSocketHandle, crate::OkxWebSocketConfig, CreateOkxPublicWebSocket, RemoveOkxPublicWebSocket, okx_public_websocket, okx_public_websocket_connections, Stream),
            (okx_private_rest, OkxPrivateRestHandle, crate::OkxPrivateRestConfig, CreateOkxPrivateRest, RemoveOkxPrivateRest, okx_private_rest, okx_private_rest_connections, Rest),
            (okx_private_websocket, OkxPrivateWebSocketHandle, crate::OkxPrivateWebSocketConfig, CreateOkxPrivateWebSocket, RemoveOkxPrivateWebSocket, okx_private_websocket, okx_private_websocket_connections, Stream)
        }
    };
}

macro_rules! define_connection_control_command {
    ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $collection:ident, $mode:ident)),* $(,)?) => {
        enum ConnectionControlCommand {
            $(
                $create {
                    key: ConnectionKey,
                    parameters: $parameters,
                    options: crate::ConnectionCreateOptions,
                    reply: oneshot::Sender<Result<(), ConnectionControlError>>,
                },
                $remove {
                    key: ConnectionKey,
                    reply: oneshot::Sender<Result<(), ConnectionControlError>>,
                },
            )*
        }
    };
}

for_each_handle_collection!(define_connection_control_command);

pub struct Conflux<A: ConfluxActor> {
    actor: A,
    system: ConfluxSystem,
    sender: mpsc::Sender<EventEnvelope<A>>,
    events: mpsc::Receiver<EventEnvelope<A>>,
    _rest_sender: mpsc::Sender<RestEnvelope<A>>,
    rest_requests: mpsc::Receiver<RestEnvelope<A>>,
    connection_controls: mpsc::Receiver<ConnectionControlCommand>,
    pending_connection_removals: HashMap<String, PendingConnectionRemoval>,
    shutdown: watch::Receiver<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
    source_tasks: Vec<tokio::task::JoinHandle<()>>,
    connection_driver: ConnectionDriverState,
    startup_connection_outputs: VecDeque<ConnectionDriverOutput>,
    wakeup_timer: Option<Pin<Box<tokio::time::Sleep>>>,
    shutdown_timeout: Duration,
}

struct PendingConnectionRemoval {
    collection: &'static str,
    key: String,
    deadline: tokio::time::Instant,
    reply: oneshot::Sender<Result<(), ConnectionControlError>>,
}

const MAX_IDLE_POLL_CADENCE: Duration = Duration::from_millis(100);

/// Async adapter around the System's cancellation-safe synchronous poll
/// surface. The outer runtime only awaits `next`; it never places `poll_fn` or
/// a synchronous `poll_*` expression directly in `tokio::select!`.
struct ConnectionDriver<'a> {
    system: &'a mut ConfluxSystem,
    state: &'a mut ConnectionDriverState,
}

impl<'a> ConnectionDriver<'a> {
    fn new(system: &'a mut ConfluxSystem, state: &'a mut ConnectionDriverState) -> Self {
        Self { system, state }
    }

    async fn next(&mut self) -> ConnectionDriverOutput {
        poll_fn(|cx| self.system.poll_all_connection_next(cx, self.state)).await
    }
}

/// Async timer source used by the outer select loop. Keeping the `Sleep`
/// behind this driver means every select operand has an async method surface;
/// resetting the deadline remains an explicit step between select turns.
struct TimerDriver<'a> {
    sleep: &'a mut Pin<Box<tokio::time::Sleep>>,
}

impl<'a> TimerDriver<'a> {
    fn new(sleep: &'a mut Pin<Box<tokio::time::Sleep>>) -> Self {
        Self { sleep }
    }

    async fn next(&mut self) -> tokio::time::Instant {
        self.sleep.as_mut().await;
        tokio::time::Instant::now()
    }
}

impl<A: ConfluxActor> Conflux<A> {
    pub fn new(
        actor: A,
        system: ConfluxSystem,
        config: ConfluxConfig,
    ) -> Result<(Self, ConfluxHandle<A>), BuildError> {
        if config.ingress_capacity == 0 {
            return Err(BuildError::ZeroIngressCapacity);
        }
        if config.shutdown_timeout.is_zero() {
            return Err(BuildError::ZeroShutdownTimeout);
        }

        let (sender, events) = mpsc::channel(config.ingress_capacity);
        let (rest_sender, rest_requests) = mpsc::channel(config.ingress_capacity);
        let (connection_control_sender, connection_controls) =
            mpsc::channel(config.ingress_capacity);
        let (shutdown_sender, shutdown) = watch::channel(None);
        let phase = Arc::new(AtomicU8::new(ProcessPhase::Created as u8));
        let handle = ConfluxHandle {
            sender: sender.clone(),
            rest_sender: rest_sender.clone(),
            shutdown: shutdown_sender,
            phase: Arc::clone(&phase),
            connection_controls: connection_control_sender,
            actor: PhantomData,
        };
        Ok((
            Self {
                actor,
                system,
                sender,
                events,
                _rest_sender: rest_sender,
                rest_requests,
                connection_controls,
                pending_connection_removals: HashMap::new(),
                shutdown,
                phase,
                source_tasks: Vec::new(),
                connection_driver: ConnectionDriverState::new(),
                startup_connection_outputs: VecDeque::new(),
                wakeup_timer: None,
                shutdown_timeout: config.shutdown_timeout,
            },
            handle,
        ))
    }

    pub async fn run(mut self) -> Result<ConfluxOutcome<A>, RunError<A::FatalError>> {
        self.set_phase(ProcessPhase::Starting);
        let lifecycle_shutdown = match self.prepare_connections().await {
            Ok(mode) => mode,
            Err(error) => {
                self.set_phase(ProcessPhase::Failed);
                let _ = tokio::time::timeout(
                    self.shutdown_timeout,
                    self.system.stop_connections(&mut self.connection_driver),
                )
                .await;
                return Err(error);
            },
        };
        let actor_shutdown = self.run_started().await.map_err(|error| {
            self.set_phase(ProcessPhase::Failed);
            RunError::Actor(error)
        })?;
        self.set_phase(ProcessPhase::Running);

        let shutdown_mode = match lifecycle_shutdown.or(actor_shutdown) {
            Some(mode) => mode,
            None => self.run_until_shutdown().await?,
        };
        self.finish_shutdown(shutdown_mode).await
    }

    async fn prepare_connections(
        &mut self,
    ) -> Result<Option<ShutdownMode>, RunError<A::FatalError>> {
        loop {
            if let Some(mode) = *self.shutdown.borrow() {
                return Ok(Some(mode));
            }
            match self.system.startup_status(&self.connection_driver) {
                Ok(true) => return Ok(None),
                Ok(false) => {},
                Err(error) => return Err(RunError::RequiredConnection(error)),
            }

            let deadline = self.next_runtime_wakeup_deadline();
            let wakeup_timer = self
                .wakeup_timer
                .get_or_insert_with(|| Box::pin(tokio::time::sleep_until(deadline)));
            wakeup_timer.as_mut().reset(deadline);

            enum StartupInput {
                Shutdown,
                Connection(ConnectionDriverOutput),
                Timer(tokio::time::Instant),
            }

            let input = {
                let mut connections =
                    ConnectionDriver::new(&mut self.system, &mut self.connection_driver);
                let mut timer = TimerDriver::new(wakeup_timer);
                tokio::select! {
                    _ = self.shutdown.changed() => StartupInput::Shutdown,
                    connection = connections.next() => StartupInput::Connection(connection),
                    now = timer.next() => StartupInput::Timer(now),
                }
            };
            match input {
                StartupInput::Shutdown => {
                    return Ok(Some(
                        self.shutdown.borrow().unwrap_or(ShutdownMode::Immediate),
                    ));
                },
                StartupInput::Connection(output) => {
                    self.startup_connection_outputs.push_back(output);
                },
                StartupInput::Timer(now) => {
                    self.system.update_timer(now, &mut self.connection_driver);
                },
            }
        }
    }

    async fn run_until_shutdown(&mut self) -> Result<ShutdownMode, RunError<A::FatalError>> {
        loop {
            if let Some(mode) = *self.shutdown.borrow() {
                return Ok(mode);
            }
            if let Some(output) = self.startup_connection_outputs.pop_front() {
                if let Some(mode) = self.run_connection_output(output).await? {
                    return Ok(mode);
                }
                continue;
            }

            let deadline = self.next_runtime_wakeup_deadline();
            let wakeup_timer = self
                .wakeup_timer
                .get_or_insert_with(|| Box::pin(tokio::time::sleep_until(deadline)));
            wakeup_timer.as_mut().reset(deadline);

            enum LoopInput<A: ConfluxActor> {
                Shutdown(Result<(), tokio::sync::watch::error::RecvError>),
                Envelope(Option<EventEnvelope<A>>),
                Rest(Option<RestEnvelope<A>>),
                Control(Option<ConnectionControlCommand>),
                Connection(ConnectionDriverOutput),
                Timer(tokio::time::Instant),
            }

            let input = {
                let mut connections =
                    ConnectionDriver::new(&mut self.system, &mut self.connection_driver);
                let mut timer = TimerDriver::new(wakeup_timer);
                tokio::select! {
                    changed = self.shutdown.changed() => LoopInput::Shutdown(changed),
                    envelope = self.events.recv() => LoopInput::Envelope(envelope),
                    request = self.rest_requests.recv() => LoopInput::Rest(request),
                    control = self.connection_controls.recv() => LoopInput::Control(control),
                    connection = connections.next() => LoopInput::Connection(connection),
                    now = timer.next() => LoopInput::Timer(now),
                }
            };

            match input {
                LoopInput::Shutdown(changed) => {
                    if changed.is_err() {
                        return Ok(ShutdownMode::Drain);
                    }
                    if let Some(mode) = *self.shutdown.borrow() {
                        return Ok(mode);
                    }
                },
                LoopInput::Envelope(envelope) => {
                    let Some(envelope) = envelope else {
                        return Ok(ShutdownMode::Drain);
                    };
                    match self.run_event(envelope).await {
                        Ok(Some(mode)) => return Ok(mode),
                        Ok(None) => {},
                        Err(error) => {
                            self.set_phase(ProcessPhase::Failed);
                            return Err(RunError::Actor(error));
                        },
                    }
                },
                LoopInput::Rest(request) => {
                    let Some(request) = request else {
                        continue;
                    };
                    match self.run_rest(request).await {
                        Ok(Some(mode)) => return Ok(mode),
                        Ok(None) => {},
                        Err(error) => {
                            self.set_phase(ProcessPhase::Failed);
                            return Err(RunError::Actor(error));
                        },
                    }
                },
                LoopInput::Control(Some(control)) => self.apply_connection_control(control).await,
                LoopInput::Control(None) => {},
                LoopInput::Connection(output) => {
                    if let Some(mode) = self.run_connection_output(output).await? {
                        return Ok(mode);
                    }
                },
                LoopInput::Timer(now) => {
                    self.system.update_timer(now, &mut self.connection_driver);
                    self.expire_connection_removals(now);
                },
            }
        }
    }

    async fn run_connection_output(
        &mut self,
        output: ConnectionDriverOutput,
    ) -> Result<Option<ShutdownMode>, RunError<A::FatalError>> {
        match output {
            ConnectionDriverOutput::Integration(event) => {
                self.run_contract_event(ConfluxEvent::Integration(event))
                    .await
            },
            ConnectionDriverOutput::System(event) => {
                self.run_contract_event(ConfluxEvent::System(event)).await
            },
            ConnectionDriverOutput::Account { client, frame } => {
                self.run_contract_event(ConfluxEvent::Account(ContractEvent { client, frame }))
                    .await
            },
            ConnectionDriverOutput::Execution { client, frame } => {
                self.run_contract_event(ConfluxEvent::Execution(ContractEvent { client, frame }))
                    .await
            },
            ConnectionDriverOutput::Market { client, frame } => {
                self.run_contract_event(ConfluxEvent::Market(ContractEvent { client, frame }))
                    .await
            },
            ConnectionDriverOutput::Reference { client, frame } => {
                self.run_contract_event(ConfluxEvent::Reference(ContractEvent { client, frame }))
                    .await
            },
            ConnectionDriverOutput::Risk { client, frame } => {
                self.run_contract_event(ConfluxEvent::Risk(ContractEvent { client, frame }))
                    .await
            },
            ConnectionDriverOutput::LifecycleStopped {
                collection,
                key,
                result,
            } => {
                self.finish_connection_removal(collection, key, result);
                Ok(None)
            },
        }
    }

    fn next_runtime_wakeup_deadline(&self) -> tokio::time::Instant {
        let idle_deadline = tokio::time::Instant::now() + MAX_IDLE_POLL_CADENCE;
        let system_deadline = self
            .system
            .next_wakeup_deadline(&self.connection_driver)
            .map_or(idle_deadline, |deadline| deadline.min(idle_deadline));
        self.pending_connection_removals
            .values()
            .map(|pending| pending.deadline)
            .min()
            .map_or(system_deadline, |deadline| deadline.min(system_deadline))
    }

    async fn apply_connection_control(&mut self, command: ConnectionControlCommand) {
        macro_rules! create {
            ($reply:expr, $field:ident, $key:expr, $parameters:expr, $options:expr, $mode:ident) => {{
                let result = self
                    .system
                    .connections()
                    .$field
                    .create_with_options($key, $parameters, $options)
                    .map_err(ConnectionControlError::from);
                let _ = $reply.send(result);
            }};
        }
        macro_rules! remove_rest {
            ($reply:expr, $collection:ident, $key:expr) => {{
                let key = $key;
                let result = match self.system.$collection.get_mut(&key.to_string()) {
                    None => Err(ConnectionControlError::NotFound(key.clone())),
                    Some(value) if value.state() == ResourceState::Retiring => {
                        Err(ConnectionControlError::Retiring(key.clone()))
                    },
                    Some(value) => {
                        value.set_state(ResourceState::Retiring);
                        self.system.$collection.remove(&key.to_string());
                        Ok(())
                    },
                };
                let _ = $reply.send(result);
            }};
        }
        macro_rules! remove_stream {
            ($reply:expr, $collection:ident, $key:expr) => {{
                let key = $key;
                match self.system.$collection.get_mut(&key.to_string()) {
                    None => {
                        let _ = $reply.send(Err(ConnectionControlError::NotFound(key.clone())));
                    },
                    Some(value) if value.state() == ResourceState::Retiring => {
                        let _ = $reply.send(Err(ConnectionControlError::Retiring(key.clone())));
                    },
                    Some(value)
                        if value.state() == ResourceState::Created
                            || (value.state() == ResourceState::Starting
                                && value.lifecycle_in_progress()) =>
                    {
                        // A not-yet-ready connection has no established
                        // session to drain. Dropping its System-owned
                        // lifecycle future cancels the handshake and drops
                        // the connection in the same resource slot.
                        self.system.$collection.remove(&key.to_string());
                        self.connection_driver
                            .clear_removed_connection(stringify!($collection), key.as_str());
                        let _ = $reply.send(Ok(()));
                    },
                    Some(value) => {
                        let identity = crate::ManagedConnectionIdentity {
                            descriptor: value.connection().descriptor().clone(),
                            generation: value.generation(),
                        };
                        value.set_state(ResourceState::Retiring);
                        self.connection_driver.purge_integration_identity(&identity);
                        self.pending_connection_removals.insert(
                            format!("{}:{}", stringify!($collection), key),
                            PendingConnectionRemoval {
                                collection: stringify!($collection),
                                key: key.to_string(),
                                deadline: tokio::time::Instant::now() + self.shutdown_timeout,
                                reply: $reply,
                            },
                        );
                    },
                }
            }};
        }
        macro_rules! remove_family {
            (Rest, $reply:expr, $collection:ident, $key:expr) => {
                remove_rest!($reply, $collection, $key)
            };
            (Stream, $reply:expr, $collection:ident, $key:expr) => {
                remove_stream!($reply, $collection, $key)
            };
        }
        macro_rules! apply_connection_control {
            ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $collection:ident, $mode:ident)),* $(,)?) => {
                match command {
                    $(
                        ConnectionControlCommand::$create { key, parameters, options, reply } => {
                            create!(reply, $typed, key, parameters, options, $mode)
                        }
                        ConnectionControlCommand::$remove { key, reply } => {
                            remove_family!($mode, reply, $collection, key)
                        }
                    )*
                }
            };
        }

        for_each_handle_collection!(apply_connection_control);
    }

    fn finish_connection_removal(
        &mut self,
        collection: &'static str,
        key: String,
        result: Result<(), String>,
    ) {
        macro_rules! remove_completed {
            ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $stored:ident, $mode:ident)),* $(,)?) => {{
                $(
                    if collection == stringify!($stored) {
                        self.system.$stored.remove(&key);
                    }
                )*
            }};
        }
        for_each_handle_collection!(remove_completed);
        self.connection_driver
            .clear_removed_connection(collection, &key);
        let Some(pending) = self
            .pending_connection_removals
            .remove(&format!("{collection}:{key}"))
        else {
            return;
        };
        let result = result.map_err(|error| ConnectionControlError::Disconnect {
            key: ConnectionKey::new(key).expect("stored connection key remains valid"),
            error,
        });
        let _ = pending.reply.send(result);
    }

    fn expire_connection_removals(&mut self, now: tokio::time::Instant) {
        let expired = self
            .pending_connection_removals
            .iter()
            .filter(|(_, pending)| pending.deadline <= now)
            .map(|(_, pending)| (pending.collection, pending.key.clone()))
            .collect::<Vec<_>>();
        for (collection, key) in expired {
            self.finish_connection_removal(
                collection,
                key,
                Err("disconnect deadline elapsed".into()),
            );
        }
    }

    async fn run_started(&mut self) -> Result<Option<ShutdownMode>, A::FatalError> {
        let mut requested_shutdown = None;
        let mut context = Context::new(
            &mut self.system,
            &mut requested_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
        );
        self.actor.started(&mut context).await?;
        Ok(requested_shutdown)
    }

    async fn run_event(
        &mut self,
        envelope: EventEnvelope<A>,
    ) -> Result<Option<ShutdownMode>, A::FatalError> {
        let response = self.run_actor_event(envelope.event).await?;
        let _ = envelope.completed.send(response.1);
        Ok(response.0)
    }

    async fn run_contract_event(
        &mut self,
        event: ActorEvent<A>,
    ) -> Result<Option<ShutdownMode>, RunError<A::FatalError>> {
        let (requested_shutdown, _) = self.run_actor_event(event).await.map_err(|error| {
            self.set_phase(ProcessPhase::Failed);
            RunError::Actor(error)
        })?;
        Ok(requested_shutdown)
    }

    async fn run_rest(
        &mut self,
        envelope: RestEnvelope<A>,
    ) -> Result<Option<ShutdownMode>, A::FatalError> {
        let response = self
            .run_actor_event(ConfluxEvent::Rest(envelope.request))
            .await?;
        let _ = envelope.completed.send(response.1);
        Ok(response.0)
    }

    async fn run_actor_event(
        &mut self,
        event: ActorEvent<A>,
    ) -> Result<(Option<ShutdownMode>, Option<RestResponseOf<A>>), A::FatalError> {
        let mut requested_shutdown = None;
        let mut context = Context::new(
            &mut self.system,
            &mut requested_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
        );
        let response = self.actor.handle(event, &mut context).await?;
        drop(context);
        Ok((requested_shutdown, response))
    }

    async fn finish_shutdown(
        mut self,
        mut mode: ShutdownMode,
    ) -> Result<ConfluxOutcome<A>, RunError<A::FatalError>> {
        let deadline = tokio::time::Instant::now() + self.shutdown_timeout;
        self.events.close();
        self.rest_requests.close();

        let mut discarded_inputs = 0;
        if mode == ShutdownMode::Drain {
            enum DrainInput<A: ConfluxActor> {
                Event(Option<EventEnvelope<A>>),
                Rest(Option<RestEnvelope<A>>),
            }
            let mut events_open = true;
            let mut rest_open = true;
            while events_open || rest_open {
                let input = match tokio::time::timeout_at(deadline, async {
                    tokio::select! {
                        envelope = self.events.recv(), if events_open => DrainInput::Event(envelope),
                        request = self.rest_requests.recv(), if rest_open => DrainInput::Rest(request),
                    }
                })
                .await
                {
                    Ok(input) => input,
                    Err(_) => {
                        mode = ShutdownMode::Immediate;
                        break;
                    }
                };
                let result = match input {
                    DrainInput::Event(Some(envelope)) => {
                        tokio::time::timeout_at(deadline, self.run_event(envelope)).await
                    },
                    DrainInput::Rest(Some(request)) => {
                        tokio::time::timeout_at(deadline, self.run_rest(request)).await
                    },
                    DrainInput::Event(None) => {
                        events_open = false;
                        continue;
                    },
                    DrainInput::Rest(None) => {
                        rest_open = false;
                        continue;
                    },
                };
                match result {
                    Ok(Ok(Some(ShutdownMode::Immediate))) => {
                        mode = ShutdownMode::Immediate;
                        break;
                    },
                    Ok(Ok(_)) => {},
                    Ok(Err(error)) => {
                        self.set_phase(ProcessPhase::Failed);
                        return Err(RunError::Actor(error));
                    },
                    Err(_) => {
                        discarded_inputs += 1;
                        mode = ShutdownMode::Immediate;
                        break;
                    },
                }
            }
        }
        if mode == ShutdownMode::Immediate {
            while self.events.try_recv().is_ok() {
                discarded_inputs += 1;
            }
            while self.rest_requests.try_recv().is_ok() {
                discarded_inputs += 1;
            }
        }

        self.set_phase(ProcessPhase::Stopping);
        let mut ignored_shutdown = None;
        let mut context = Context::new(
            &mut self.system,
            &mut ignored_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
        );
        match tokio::time::timeout_at(deadline, self.actor.stopping(&mut context)).await {
            Ok(Ok(())) => {},
            Ok(Err(error)) => {
                self.set_phase(ProcessPhase::Failed);
                return Err(RunError::Actor(error));
            },
            Err(_) => mode = ShutdownMode::Immediate,
        }
        for task in self.source_tasks.drain(..) {
            task.abort();
        }
        if tokio::time::timeout_at(
            deadline,
            self.system.stop_connections(&mut self.connection_driver),
        )
        .await
        .is_err()
        {
            mode = ShutdownMode::Immediate;
        }

        let phase = match mode {
            ShutdownMode::Drain => ProcessPhase::Stopped,
            ShutdownMode::Immediate => ProcessPhase::Forced,
        };
        self.set_phase(phase);
        Ok(ConfluxOutcome {
            actor: self.actor,
            system: self.system,
            phase,
            discarded_inputs,
        })
    }

    fn set_phase(&self, phase: ProcessPhase) {
        self.phase.store(phase as u8, Ordering::Release);
    }
}

pub struct ConfluxHandle<A: ConfluxActor> {
    sender: mpsc::Sender<EventEnvelope<A>>,
    rest_sender: mpsc::Sender<RestEnvelope<A>>,
    shutdown: watch::Sender<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
    connection_controls: mpsc::Sender<ConnectionControlCommand>,
    actor: PhantomData<fn() -> A>,
}

impl<A: ConfluxActor> Clone for ConfluxHandle<A> {
    fn clone(&self) -> Self {
        Self {
            sender: self.sender.clone(),
            rest_sender: self.rest_sender.clone(),
            shutdown: self.shutdown.clone(),
            phase: Arc::clone(&self.phase),
            connection_controls: self.connection_controls.clone(),
            actor: PhantomData,
        }
    }
}

macro_rules! define_handle_connections_method {
    ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $collection:ident, $mode:ident)),* $(,)?) => {
        pub fn connections(&self) -> HandleConnectionCollections<'_, A> {
            HandleConnectionCollections {
                $($field: $handle { handle: self },)*
            }
        }
    };
}

macro_rules! define_handle_connection_collections {
    ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $collection:ident, $mode:ident)),* $(,)?) => {
        pub struct HandleConnectionCollections<'a, A: ConfluxActor> {
            $(pub $field: $handle<'a, A>,)*
        }

        $(
            pub struct $handle<'a, A: ConfluxActor> {
                handle: &'a ConfluxHandle<A>,
            }

            impl<A: ConfluxActor> $handle<'_, A> {
                pub async fn create(
                    &self,
                    key: ConnectionKey,
                    parameters: $parameters,
                ) -> Result<(), ConnectionControlError> {
                    self.create_with_options(
                        key,
                        parameters,
                        crate::ConnectionCreateOptions::default(),
                    )
                    .await
                }

                pub async fn create_with_options(
                    &self,
                    key: ConnectionKey,
                    parameters: $parameters,
                    options: crate::ConnectionCreateOptions,
                ) -> Result<(), ConnectionControlError> {
                    self.handle
                        .connection_control(|reply| ConnectionControlCommand::$create {
                            key,
                            parameters,
                            options,
                            reply,
                        })
                        .await
                }

                pub async fn remove(
                    &self,
                    key: ConnectionKey,
                ) -> Result<(), ConnectionControlError> {
                    self.handle
                        .connection_control(|reply| ConnectionControlCommand::$remove {
                            key,
                            reply,
                        })
                        .await
                }
            }
        )*
    };
}

impl<A: ConfluxActor> ConfluxHandle<A> {
    /// Routes REST requests to the dedicated REST ingress and all other
    /// externally submitted events to the local ingress.
    pub async fn handle(
        &self,
        event: ActorEvent<A>,
    ) -> Result<Option<RestResponseOf<A>>, HandleError<ActorEvent<A>>> {
        let event = match event {
            ConfluxEvent::Rest(request) => {
                return self
                    .handle_rest(request)
                    .await
                    .map_err(|error| match error {
                        HandleError::Closed(request) => {
                            HandleError::Closed(ConfluxEvent::Rest(request))
                        },
                        HandleError::ActorStopped => HandleError::ActorStopped,
                    });
            },
            event => event,
        };
        let (completed, response) = oneshot::channel();
        self.sender
            .send(EventEnvelope { event, completed })
            .await
            .map_err(|error| HandleError::Closed(error.0.event))?;
        response.await.map_err(|_| HandleError::ActorStopped)
    }

    pub async fn handle_rest(
        &self,
        request: RestRequestOf<A>,
    ) -> Result<Option<RestResponseOf<A>>, HandleError<RestRequestOf<A>>> {
        let (completed, response) = oneshot::channel();
        self.rest_sender
            .send(RestEnvelope { request, completed })
            .await
            .map_err(|error| HandleError::Closed(error.0.request))?;
        response.await.map_err(|_| HandleError::ActorStopped)
    }

    pub fn shutdown(&self, mode: ShutdownMode) {
        self.shutdown.send_replace(Some(mode));
    }

    pub fn phase(&self) -> ProcessPhase {
        ProcessPhase::from_u8(self.phase.load(Ordering::Acquire))
    }

    for_each_handle_collection!(define_handle_connections_method);

    async fn connection_control(
        &self,
        build: impl FnOnce(
            oneshot::Sender<Result<(), ConnectionControlError>>,
        ) -> ConnectionControlCommand,
    ) -> Result<(), ConnectionControlError> {
        let phase = self.phase();
        if matches!(
            phase,
            ProcessPhase::Stopping
                | ProcessPhase::Stopped
                | ProcessPhase::Forced
                | ProcessPhase::Failed
        ) || self.shutdown.borrow().is_some()
        {
            return Err(ConnectionControlError::Unavailable(phase));
        }
        let (reply, response) = oneshot::channel();
        let mut shutdown = self.shutdown.subscribe();
        self.connection_controls
            .send(build(reply))
            .await
            .map_err(|_| ConnectionControlError::Closed)?;
        tokio::select! {
            result = response => result.map_err(|_| ConnectionControlError::Closed)?,
            _ = shutdown.changed() => Err(ConnectionControlError::Unavailable(self.phase())),
        }
    }
}

for_each_handle_collection!(define_handle_connection_collections);

#[derive(PartialEq, Eq)]
pub enum HandleError<E> {
    Closed(E),
    ActorStopped,
}

impl<E> std::fmt::Debug for HandleError<E> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Closed(_) => formatter.write_str("Closed(..)"),
            Self::ActorStopped => formatter.write_str("ActorStopped"),
        }
    }
}

pub struct ConfluxOutcome<A: ConfluxActor> {
    pub actor: A,
    pub system: ConfluxSystem,
    pub phase: ProcessPhase,
    pub discarded_inputs: usize,
}

#[derive(Debug, Error)]
pub enum RunError<E> {
    #[error("Actor failed: {0}")]
    Actor(E),
    #[error("required Integration connection failed during startup: {0}")]
    RequiredConnection(String),
}

#[cfg(test)]
mod tests {
    use std::convert::Infallible;

    use futures_util::{SinkExt, StreamExt};

    use super::*;
    use crate::{Contract, RestContract, SystemEvent};

    struct TestRest;

    impl RestContract for TestRest {
        type Request = i64;
        type Response = i64;
    }

    #[derive(Default)]
    struct TestActor {
        total: i64,
    }

    struct SourceReadyActor {
        ready: Option<oneshot::Sender<()>>,
    }

    struct StartupReadyActor {
        key: ConnectionKey,
        started_ready: Option<oneshot::Sender<bool>>,
    }

    struct IntegrationKeysActor {
        keys: std::collections::BTreeSet<ConnectionKey>,
    }

    struct ReadyCountActor {
        ready: usize,
    }

    impl Contract for TestActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for TestActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            match event {
                ConfluxEvent::Rest(value) => Ok(Some(self.total + value)),
                ConfluxEvent::Local(value) => {
                    self.total += value;
                    Ok(None)
                },
                _ => Ok(None),
            }
        }
    }

    impl Contract for SourceReadyActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for SourceReadyActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            if matches!(event, ConfluxEvent::System(SystemEvent::SourceReady { .. })) {
                if let Some(ready) = self.ready.take() {
                    let _ = ready.send(());
                }
            }
            Ok(None)
        }
    }

    impl Contract for StartupReadyActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for StartupReadyActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn started(
            &mut self,
            context: &mut Context<'_, Self>,
        ) -> Result<(), Self::FatalError> {
            let ready = context
                .connections()
                .binance_spot_websocket
                .get(&self.key)
                .is_ok();
            if let Some(started_ready) = self.started_ready.take() {
                let _ = started_ready.send(ready);
            }
            Ok(())
        }

        async fn handle(
            &mut self,
            _event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            Ok(None)
        }
    }

    impl Contract for IntegrationKeysActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for IntegrationKeysActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            if let ConfluxEvent::Integration(event) = event {
                self.keys
                    .insert(event.identity.descriptor.connection_key.clone());
                if self.keys.len() == 2 {
                    context.request_shutdown(ShutdownMode::Drain);
                }
            }
            Ok(None)
        }
    }

    impl Contract for ReadyCountActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for ReadyCountActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            if matches!(event, ConfluxEvent::System(SystemEvent::SourceReady { .. })) {
                self.ready += 1;
                if self.ready == 2 {
                    context.request_shutdown(ShutdownMode::Drain);
                }
            }
            Ok(None)
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn one_handle_serializes_rest_and_event_inputs() {
        let (conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig {
                ingress_capacity: 8,
                ..ConfluxConfig::default()
            },
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());

                assert_eq!(handle.handle(ConfluxEvent::Local(7)).await.unwrap(), None);
                assert_eq!(
                    handle.handle(ConfluxEvent::Rest(5)).await.unwrap(),
                    Some(12)
                );

                handle.shutdown(ShutdownMode::Drain);
                let outcome = process.await.unwrap().unwrap();
                assert_eq!(outcome.actor.total, 7);
                assert_eq!(outcome.phase, ProcessPhase::Stopped);
                assert_eq!(outcome.discarded_inputs, 0);
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn handle_can_create_remove_and_recreate_a_typed_connection() {
        let (conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let key = ConnectionKey::new("dynamic-okx").unwrap();
                let parameters = crate::OkxRestConfig {
                    environment: "test".into(),
                    endpoint: "https://www.okx.com".into(),
                };

                handle
                    .connections()
                    .okx_public_rest
                    .create(key.clone(), parameters.clone())
                    .await
                    .unwrap();
                handle
                    .connections()
                    .okx_public_rest
                    .remove(key.clone())
                    .await
                    .unwrap();
                handle
                    .connections()
                    .okx_public_rest
                    .create(key.clone(), parameters)
                    .await
                    .unwrap();

                handle.shutdown(ShutdownMode::Drain);
                let mut outcome = process.await.unwrap().unwrap();
                assert_eq!(
                    outcome
                        .system
                        .connections()
                        .okx_public_rest
                        .generation(&key)
                        .unwrap(),
                    2
                );
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn duplicate_create_and_missing_remove_have_stable_errors() {
        let (conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let key = ConnectionKey::new("duplicate-okx").unwrap();
                let parameters = crate::OkxRestConfig {
                    environment: "test".into(),
                    endpoint: "https://www.okx.com".into(),
                };
                handle
                    .connections()
                    .okx_public_rest
                    .create(key.clone(), parameters.clone())
                    .await
                    .unwrap();

                assert!(matches!(
                    handle
                        .connections()
                        .okx_public_rest
                        .create(key.clone(), parameters)
                        .await,
                    Err(ConnectionControlError::Create(
                        ConnectionCreateError::AlreadyExists(value)
                    )) if value == key
                ));
                handle
                    .connections()
                    .okx_public_rest
                    .remove(key.clone())
                    .await
                    .unwrap();
                assert!(matches!(
                    handle.connections().okx_public_rest.remove(key.clone()).await,
                    Err(ConnectionControlError::NotFound(value)) if value == key
                ));

                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn pending_stream_connect_does_not_block_rest_or_dynamic_removal() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("ws://{}", listener.local_addr().unwrap());
        let (accepted, accepted_rx) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (_socket, _) = listener.accept().await.unwrap();
            let _ = accepted.send(());
            std::future::pending::<()>().await;
        });
        let (conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let key = ConnectionKey::new("pending-binance-stream").unwrap();
                handle
                    .connections()
                    .binance_spot_websocket
                    .create(
                        key.clone(),
                        crate::BinanceWebSocketConfig {
                            environment: "test".into(),
                            endpoint,
                            credential: None,
                            event_capacity: 8,
                        },
                    )
                    .await
                    .unwrap();
                tokio::time::timeout(Duration::from_secs(1), accepted_rx)
                    .await
                    .expect("Conflux must begin the stream handshake")
                    .unwrap();

                assert_eq!(
                    tokio::time::timeout(Duration::from_millis(100), handle.handle_rest(7))
                        .await
                        .expect("pending connect must not block REST")
                        .unwrap(),
                    Some(7)
                );
                tokio::time::timeout(
                    Duration::from_millis(100),
                    handle.connections().binance_spot_websocket.remove(key),
                )
                .await
                .expect("removing a pending handshake must not block")
                .unwrap();

                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
        server.abort();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn removing_a_ready_stream_disconnects_before_releasing_the_slot() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("ws://{}", listener.local_addr().unwrap());
        let (peer_closed, peer_closed_rx) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            while socket.next().await.is_some() {}
            let _ = peer_closed.send(());
        });
        let (ready, ready_rx) = oneshot::channel();
        let (conflux, handle) = Conflux::new(
            SourceReadyActor { ready: Some(ready) },
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                let key = ConnectionKey::new("ready-binance-stream").unwrap();
                handle
                    .connections()
                    .binance_spot_websocket
                    .create(
                        key.clone(),
                        crate::BinanceWebSocketConfig {
                            environment: "test".into(),
                            endpoint,
                            credential: None,
                            event_capacity: 8,
                        },
                    )
                    .await
                    .unwrap();
                tokio::time::timeout(Duration::from_secs(1), ready_rx)
                    .await
                    .expect("connection must become ready")
                    .unwrap();

                tokio::time::timeout(
                    Duration::from_secs(1),
                    handle.connections().binance_spot_websocket.remove(key),
                )
                .await
                .expect("ready connection removal must complete")
                .unwrap();
                tokio::time::timeout(Duration::from_secs(1), peer_closed_rx)
                    .await
                    .expect("peer must observe disconnect")
                    .unwrap();

                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn graceful_shutdown_disconnects_managed_stream_before_returning() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("ws://{}", listener.local_addr().unwrap());
        let (peer_closed, peer_closed_rx) = oneshot::channel();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            while socket.next().await.is_some() {}
            let _ = peer_closed.send(());
        });
        let (ready, ready_rx) = oneshot::channel();
        let (conflux, handle) = Conflux::new(
            SourceReadyActor { ready: Some(ready) },
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                handle
                    .connections()
                    .binance_spot_websocket
                    .create(
                        ConnectionKey::new("shutdown-binance-stream").unwrap(),
                        crate::BinanceWebSocketConfig {
                            environment: "test".into(),
                            endpoint,
                            credential: None,
                            event_capacity: 8,
                        },
                    )
                    .await
                    .unwrap();
                tokio::time::timeout(Duration::from_secs(1), ready_rx)
                    .await
                    .expect("connection must become ready")
                    .unwrap();

                handle.shutdown(ShutdownMode::Drain);
                let outcome = tokio::time::timeout(Duration::from_secs(1), process)
                    .await
                    .expect("graceful shutdown must complete")
                    .unwrap()
                    .unwrap();
                assert_eq!(outcome.phase, ProcessPhase::Stopped);
                tokio::time::timeout(Duration::from_secs(1), peer_closed_rx)
                    .await
                    .expect("peer must observe disconnect before shutdown returns")
                    .unwrap();
            })
            .await;
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn preinstalled_stream_is_ready_before_actor_started() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("ws://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            while socket.next().await.is_some() {}
        });
        let key = ConnectionKey::new("startup-binance-stream").unwrap();
        let mut system = ConfluxSystem::new();
        system
            .connections()
            .binance_spot_websocket
            .create(
                key.clone(),
                crate::BinanceWebSocketConfig {
                    environment: "test".into(),
                    endpoint,
                    credential: None,
                    event_capacity: 8,
                },
            )
            .unwrap();
        let (started_ready, started_ready_rx) = oneshot::channel();
        let (conflux, handle) = Conflux::new(
            StartupReadyActor {
                key,
                started_ready: Some(started_ready),
            },
            system,
            ConfluxConfig::default(),
        )
        .unwrap();

        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                assert!(
                    tokio::time::timeout(Duration::from_secs(1), started_ready_rx)
                        .await
                        .expect("Actor startup must follow connection startup")
                        .unwrap()
                );
                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn one_driver_pass_registers_and_delivers_all_ready_connections() {
        async fn websocket_server(listener: tokio::net::TcpListener, symbol: &'static str) {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    format!(r#"{{"e":"trade","s":"{symbol}","E":1,"p":"1","q":"1"}}"#).into(),
                ))
                .await
                .unwrap();
            while socket.next().await.is_some() {}
        }

        let first = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let second = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let first_endpoint = format!("ws://{}", first.local_addr().unwrap());
        let second_endpoint = format!("ws://{}", second.local_addr().unwrap());
        let first_server = tokio::spawn(websocket_server(first, "BTCUSDT"));
        let second_server = tokio::spawn(websocket_server(second, "ETHUSDT"));

        let first_key = ConnectionKey::new("market-first").unwrap();
        let second_key = ConnectionKey::new("market-second").unwrap();
        let mut system = ConfluxSystem::new();
        for (key, endpoint) in [
            (first_key.clone(), first_endpoint),
            (second_key.clone(), second_endpoint),
        ] {
            system
                .connections()
                .binance_spot_websocket
                .create(
                    key,
                    crate::BinanceWebSocketConfig {
                        environment: "test".into(),
                        endpoint,
                        credential: None,
                        event_capacity: 8,
                    },
                )
                .unwrap();
        }
        let (conflux, _handle) = Conflux::new(
            IntegrationKeysActor {
                keys: std::collections::BTreeSet::new(),
            },
            system,
            ConfluxConfig::default(),
        )
        .unwrap();

        let outcome = tokio::time::timeout(Duration::from_secs(1), conflux.run())
            .await
            .expect("both managed connections must be polled")
            .unwrap();
        assert_eq!(
            outcome.actor.keys,
            std::collections::BTreeSet::from([first_key, second_key])
        );
        first_server.await.unwrap();
        second_server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn required_connection_failure_prevents_actor_startup() {
        let key = ConnectionKey::new("required-binance-stream").unwrap();
        let mut system = ConfluxSystem::new();
        system
            .connections()
            .binance_spot_websocket
            .create_with_options(
                key.clone(),
                crate::BinanceWebSocketConfig {
                    environment: "test".into(),
                    endpoint: "ws://127.0.0.1:9".into(),
                    credential: None,
                    event_capacity: 8,
                },
                crate::ConnectionCreateOptions {
                    required: true,
                    recovery: crate::RecoveryPolicy {
                        maximum_attempts: Some(0),
                        ..crate::RecoveryPolicy::default()
                    },
                },
            )
            .unwrap();
        let (started_ready, started_ready_rx) = oneshot::channel();
        let (conflux, _handle) = Conflux::new(
            StartupReadyActor {
                key,
                started_ready: Some(started_ready),
            },
            system,
            ConfluxConfig::default(),
        )
        .unwrap();

        let result = tokio::time::timeout(Duration::from_secs(1), conflux.run())
            .await
            .expect("required connection failure must be bounded");
        assert!(matches!(result, Err(RunError::RequiredConnection(_))));
        assert!(started_ready_rx.await.is_err());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn optional_connection_failure_still_allows_actor_startup() {
        let key = ConnectionKey::new("optional-binance-stream").unwrap();
        let mut system = ConfluxSystem::new();
        system
            .connections()
            .binance_spot_websocket
            .create_with_options(
                key.clone(),
                crate::BinanceWebSocketConfig {
                    environment: "test".into(),
                    endpoint: "ws://127.0.0.1:9".into(),
                    credential: None,
                    event_capacity: 8,
                },
                crate::ConnectionCreateOptions {
                    required: false,
                    recovery: crate::RecoveryPolicy {
                        maximum_attempts: Some(0),
                        ..crate::RecoveryPolicy::default()
                    },
                },
            )
            .unwrap();
        let (started_ready, started_ready_rx) = oneshot::channel();
        let (conflux, handle) = Conflux::new(
            StartupReadyActor {
                key,
                started_ready: Some(started_ready),
            },
            system,
            ConfluxConfig::default(),
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());
                assert!(
                    !tokio::time::timeout(Duration::from_secs(1), started_ready_rx)
                        .await
                        .expect("optional failure must not prevent Actor startup")
                        .unwrap()
                );
                handle.shutdown(ShutdownMode::Drain);
                process.await.unwrap().unwrap();
            })
            .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn transport_failure_reconnects_with_bounded_backoff() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("ws://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let (first, _) = listener.accept().await.unwrap();
            let mut first = tokio_tungstenite::accept_async(first).await.unwrap();
            first.close(None).await.unwrap();

            let (second, _) = listener.accept().await.unwrap();
            let mut second = tokio_tungstenite::accept_async(second).await.unwrap();
            while second.next().await.is_some() {}
        });
        let mut system = ConfluxSystem::new();
        system
            .connections()
            .binance_spot_websocket
            .create_with_options(
                ConnectionKey::new("recovering-binance-stream").unwrap(),
                crate::BinanceWebSocketConfig {
                    environment: "test".into(),
                    endpoint,
                    credential: None,
                    event_capacity: 8,
                },
                crate::ConnectionCreateOptions {
                    required: true,
                    recovery: crate::RecoveryPolicy {
                        initial_backoff: Duration::from_millis(10),
                        maximum_backoff: Duration::from_millis(10),
                        maximum_attempts: Some(2),
                    },
                },
            )
            .unwrap();
        let (conflux, _handle) = Conflux::new(
            ReadyCountActor { ready: 0 },
            system,
            ConfluxConfig::default(),
        )
        .unwrap();

        let outcome = tokio::time::timeout(Duration::from_secs(1), conflux.run())
            .await
            .expect("recoverable transport failure must reconnect")
            .unwrap();
        assert_eq!(outcome.actor.ready, 2);
        server.await.unwrap();
    }

    #[test]
    fn handle_exposes_every_system_typed_connection_collection() {
        fn assert_api<A: ConfluxActor>(handle: &ConfluxHandle<A>) {
            macro_rules! assert_fields {
                ($(($field:ident, $handle:ident, $parameters:ty, $create:ident, $remove:ident, $typed:ident, $collection:ident, $mode:ident)),* $(,)?) => {
                    $(let _: &$handle<'_, A> = &handle.connections().$field;)*
                };
            }
            for_each_handle_collection!(assert_fields);
        }
        let _ = assert_api::<TestActor>;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn connection_mutation_is_rejected_after_shutdown_is_requested() {
        let (_conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();
        handle.shutdown(ShutdownMode::Drain);
        let error = handle
            .connections()
            .okx_public_rest
            .create(
                ConnectionKey::new("late-okx").unwrap(),
                crate::OkxRestConfig {
                    environment: "test".into(),
                    endpoint: "https://www.okx.com".into(),
                },
            )
            .await
            .unwrap_err();
        assert!(matches!(error, ConnectionControlError::Unavailable(_)));
    }

    #[test]
    fn zero_shutdown_timeout_is_rejected() {
        assert!(matches!(
            Conflux::new(
                TestActor::default(),
                ConfluxSystem::new(),
                ConfluxConfig {
                    shutdown_timeout: Duration::ZERO,
                    ..ConfluxConfig::default()
                },
            ),
            Err(BuildError::ZeroShutdownTimeout)
        ));
    }

    struct TimerActor {
        ticks: usize,
    }

    impl Contract for TimerActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for TimerActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn started(
            &mut self,
            context: &mut Context<'_, Self>,
        ) -> Result<(), Self::FatalError> {
            context.spawn_timer("tick", Duration::from_millis(1));
            Ok(())
        }

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            if let ConfluxEvent::System(SystemEvent::Timer { name, .. }) = event {
                assert_eq!(name, "tick");
                self.ticks += 1;
                context.request_shutdown(ShutdownMode::Drain);
            }
            Ok(None)
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn system_owned_timer_is_driven_by_the_async_timer_branch() {
        let (conflux, _handle) = Conflux::new(
            TimerActor { ticks: 0 },
            ConfluxSystem::new(),
            ConfluxConfig::default(),
        )
        .unwrap();

        let outcome = tokio::time::timeout(Duration::from_secs(1), conflux.run())
            .await
            .expect("timer branch should wake the runtime")
            .unwrap();
        assert_eq!(outcome.actor.ticks, 1);
    }

    #[test]
    fn far_system_deadline_is_clamped_to_the_non_waking_ingress_cadence() {
        let mut system = ConfluxSystem::new();
        system.register_timer("far".into(), Duration::from_secs(60));
        let (conflux, _handle) =
            Conflux::new(TestActor::default(), system, ConfluxConfig::default()).unwrap();
        let before = tokio::time::Instant::now();

        let deadline = conflux.next_runtime_wakeup_deadline();

        assert!(deadline >= before);
        assert!(deadline <= before + MAX_IDLE_POLL_CADENCE + Duration::from_millis(1));
    }

    #[test]
    fn outer_select_only_awaits_async_driver_or_channel_methods() {
        let source = include_str!("process.rs");
        let runtime = &source[source
            .find("async fn run_until_shutdown")
            .expect("runtime loop must exist")..];
        let start = runtime
            .find("let input = {\n                let mut connections")
            .expect("outer runtime select must remain recognizable");
        let select = &runtime[start..];
        let end = select
            .find("\n            match input")
            .expect("outer runtime select must end before input dispatch");
        let select = &select[..end];
        let select = &select[select
            .find("tokio::select! {")
            .expect("outer runtime must use tokio::select!")..];

        for forbidden in [
            "poll_fn(",
            "poll_all_connection_next(",
            "poll_due_maintenance(",
            "has_due_maintenance(",
            "next_wakeup_deadline(",
            "update_timer(",
            "async {",
        ] {
            assert!(
                !select.contains(forbidden),
                "synchronous `{forbidden}` must stay outside tokio::select!"
            );
        }
        for required in [
            "self.shutdown.changed()",
            "self.events.recv()",
            "self.rest_requests.recv()",
            "self.connection_controls.recv()",
            "connections.next()",
            "timer.next()",
        ] {
            assert!(
                select.contains(required),
                "outer select must await async source `{required}`"
            );
        }
    }

    #[test]
    fn connection_lifecycle_is_polled_instead_of_awaited_by_the_actor_loop() {
        let process = include_str!("process.rs");
        let system = include_str!("system.rs");
        let resource = include_str!("resource.rs");
        assert!(!process.contains(concat!("start_installed", "_connections")));
        assert!(!process.contains(concat!("ConnectionLifecycleCommand::", "connect")));
        assert!(!process.contains(concat!("ConnectionLifecycleCommand::", "disconnect")));
        assert!(system.contains("poll_all_connection_lifecycle(cx"));
        assert!(resource.contains("ManagedLifecycleFuture"));
        assert!(resource.contains("begin_lifecycle"));
        assert!(resource.contains("poll_lifecycle"));
    }

    struct StuckStoppingActor;

    impl Contract for StuckStoppingActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for StuckStoppingActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            _event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            Ok(None)
        }

        async fn stopping(
            &mut self,
            _context: &mut Context<'_, Self>,
        ) -> Result<(), Self::FatalError> {
            std::future::pending().await
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn shutdown_deadline_forces_a_stuck_actor() {
        let (conflux, handle) = Conflux::new(
            StuckStoppingActor,
            ConfluxSystem::new(),
            ConfluxConfig {
                shutdown_timeout: Duration::from_millis(10),
                ..ConfluxConfig::default()
            },
        )
        .unwrap();
        handle.shutdown(ShutdownMode::Drain);
        let outcome = conflux.run().await.unwrap();
        assert_eq!(outcome.phase, ProcessPhase::Forced);
    }
}
