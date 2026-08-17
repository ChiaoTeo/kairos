//! Process lifecycle helpers with no business-state ownership.

use super::*;
use std::path::Path;

pub(super) fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

pub(super) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

impl
    ExecutionProcess<
        NoAsyncOrderEntryConnection,
        NoAsyncOrderQueryConnection,
        NoAsyncOrderEventSource,
    >
{
    pub fn new(application: ExecutionApplication, socket_path: impl Into<PathBuf>) -> Self {
        Self {
            application,
            simulator: None,
            socket_path: socket_path.into(),
            audit: None,
            simulated_account_settlement: None,
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            event_publisher: None,
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
            last_remote_reconcile_unix_nanos: 0,
            async_order_entry: None,
            async_order_query: None,
            async_execution_streams: Vec::new(),
            route_readiness: Default::default(),
        }
    }

    pub fn with_audit(
        application: ExecutionApplication,
        socket_path: impl Into<PathBuf>,
        audit: impl Into<ExecutionAudit>,
    ) -> Self {
        Self {
            application,
            simulator: None,
            socket_path: socket_path.into(),
            audit: Some(audit.into()),
            simulated_account_settlement: None,
            stopping: false,
            last_published_generation: None,
            snapshot_publisher: None,
            intent_snapshot_publisher: None,
            event_publisher: None,
            metrics: std::sync::Arc::new(RuntimeMetrics::default()),
            last_remote_reconcile_unix_nanos: 0,
            async_order_entry: None,
            async_order_query: None,
            async_execution_streams: Vec::new(),
            route_readiness: Default::default(),
        }
    }
}

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub fn with_async_order_entry<T>(self, connection: Option<T>) -> ExecutionProcess<T, Q, S> {
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            simulated_account_settlement: self.simulated_account_settlement,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            event_publisher: self.event_publisher,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: connection,
            async_order_query: self.async_order_query,
            async_execution_streams: self.async_execution_streams,
            route_readiness: self.route_readiness,
        }
    }

    pub fn with_async_order_query<T>(self, connection: Option<T>) -> ExecutionProcess<E, T, S> {
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            simulated_account_settlement: self.simulated_account_settlement,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            event_publisher: self.event_publisher,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: self.async_order_entry,
            async_order_query: connection,
            async_execution_streams: self.async_execution_streams,
            route_readiness: self.route_readiness,
        }
    }

    pub fn with_async_execution_stream<T>(self, source: Option<T>) -> ExecutionProcess<E, Q, T> {
        self.with_async_execution_streams(source.into_iter().collect())
    }

    pub fn with_async_execution_streams<T>(self, sources: Vec<T>) -> ExecutionProcess<E, Q, T> {
        let routes = sources
            .into_iter()
            .enumerate()
            .map(|(index, source)| {
                ExecutionAsyncRoute::new(format!("async-route-{index}"), true, source)
            })
            .collect();
        self.with_async_execution_routes(routes)
    }

    pub fn with_async_execution_routes<T>(
        self,
        routes: Vec<ExecutionAsyncRoute<T>>,
    ) -> ExecutionProcess<E, Q, T> {
        let readiness = routes
            .iter()
            .map(|route| ExecutionRouteReadiness {
                route_id: route.route_id.clone(),
                required: route.required,
                binding_id: route.binding_id.clone(),
                status: "created",
                last_error: None,
            })
            .collect();
        ExecutionProcess {
            application: self.application,
            simulator: self.simulator,
            socket_path: self.socket_path,
            audit: self.audit,
            simulated_account_settlement: self.simulated_account_settlement,
            stopping: self.stopping,
            last_published_generation: self.last_published_generation,
            snapshot_publisher: self.snapshot_publisher,
            intent_snapshot_publisher: self.intent_snapshot_publisher,
            event_publisher: self.event_publisher,
            metrics: self.metrics,
            last_remote_reconcile_unix_nanos: self.last_remote_reconcile_unix_nanos,
            async_order_entry: self.async_order_entry,
            async_order_query: self.async_order_query,
            async_execution_streams: routes,
            route_readiness: std::sync::Arc::new(std::sync::Mutex::new(readiness)),
        }
    }

    pub fn with_simulator(mut self, simulator: ExecutionSimulator) -> Self {
        self.simulator = Some(simulator);
        self
    }

    pub fn with_simulated_account_settlement(
        mut self,
        settlement: SimulatedAccountSettlement,
    ) -> Self {
        self.simulated_account_settlement = Some(settlement);
        self
    }

    pub fn with_snapshot_publisher(mut self, publisher: SharedExecutionSnapshotPublisher) -> Self {
        self.snapshot_publisher = Some(publisher);
        self
    }

    pub fn with_intent_snapshot_publisher(
        mut self,
        publisher: SharedIntentSnapshotPublisher,
    ) -> Self {
        self.intent_snapshot_publisher = Some(publisher);
        self
    }

    pub fn with_event_publisher(mut self, publisher: AeronExecutionEventPublisher) -> Self {
        self.event_publisher = Some(ExecutionEventPublication::Aeron(publisher));
        self
    }

    #[cfg(test)]
    pub(in crate::application::process) fn with_event_publication(
        mut self,
        publication: ExecutionEventPublication,
    ) -> Self {
        self.event_publisher = Some(publication);
        self
    }
}

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub async fn run(mut self) -> Result<(), Box<dyn std::error::Error>>
    where
        E: AsyncOrderEntryConnection + 'static,
        Q: AsyncOrderQueryConnection + 'static,
        S: AsyncOrderEventSource + 'static,
    {
        info!(event = "process_starting", component = "execution", socket = %self.socket_path.display(), "execution process starting");
        // A control socket is not business readiness. Required private order
        // streams must finish provider authentication/subscription before the
        // process can advertise ready or accept live commands.
        self.connect_async_execution_streams().await?;
        remove_socket(&self.socket_path)?;
        if let Some(parent) = self.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&self.socket_path)?;
        let (command_sender, command_receiver) = std::sync::mpsc::sync_channel(256);
        let (query_sender, query_receiver) = std::sync::mpsc::sync_channel(512);
        let (exchange_sender, exchange_receiver) = std::sync::mpsc::sync_channel(4096);
        // Keep the exchange mailbox alive even when no provider stream is
        // configured.  A missing stream is a valid degraded mode, not a
        // signal for the state owner to shut down.
        let _exchange_sender_guard = exchange_sender.clone();
        let stream_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stream_task = self.start_stream_consumer(
            exchange_sender.clone(),
            stream_stop.clone(),
            std::sync::Arc::clone(&self.metrics),
        );
        let (async_stream_shutdown, async_stream_shutdown_rx) = tokio::sync::watch::channel(false);
        let async_stream_tasks = self.start_async_stream_consumers(
            exchange_sender,
            async_stream_shutdown_rx,
            std::sync::Arc::clone(&self.metrics),
        );
        let (async_gateway_shutdown, async_gateway_shutdown_rx) =
            tokio::sync::watch::channel(false);
        let async_gateway_task = self.start_async_gateway_worker(async_gateway_shutdown_rx.clone());
        let async_query_gateway_task =
            self.start_async_query_gateway_worker(async_gateway_shutdown_rx);
        let gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let gateway_task = if async_gateway_task.is_none() {
            self.start_gateway_worker(gateway_stop.clone())
        } else {
            None
        };
        let query_gateway_stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let query_gateway_task = if async_query_gateway_task.is_none() {
            self.start_query_gateway_worker(query_gateway_stop.clone())
        } else {
            None
        };
        let ingress = ControlIngress::new(
            command_sender,
            query_sender,
            std::sync::Arc::clone(&self.metrics),
        );
        let server = start_control_transport(listener, ingress);
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_ready", component = "execution", socket = %self.socket_path.display(), "execution control socket ready");
        let socket_path = self.socket_path.clone();
        let state_task = tokio::task::spawn_blocking(move || {
            self.state_loop(command_receiver, query_receiver, exchange_receiver)
        });
        state_task
            .await
            .map_err(|error| format!("execution state task failed: {error}"))?
            .map_err(|error| error.to_string())?;
        stream_stop.store(true, std::sync::atomic::Ordering::Release);
        let _ = async_stream_shutdown.send(true);
        for task in async_stream_tasks {
            if let Err(error) = task.await {
                tracing::warn!(
                    event = "async_exchange_stream_task_failed",
                    component = "execution",
                    error = %error,
                    "async exchange stream task failed"
                );
            }
        }
        if let Some(task) = stream_task {
            if task.is_finished() {
                let _ = task.join();
            } else {
                tracing::warn!(
                    event = "exchange_stream_shutdown_deferred",
                    component = "execution",
                    "exchange stream did not stop before process teardown"
                );
            }
        }
        gateway_stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(task) = gateway_task {
            let _ = task.join();
        }
        query_gateway_stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(task) = query_gateway_task {
            let _ = task.join();
        }
        let _ = async_gateway_shutdown.send(true);
        if let Some(task) = async_gateway_task {
            if let Err(error) = task.await {
                tracing::warn!(event = "async_order_gateway_task_failed", component = "execution", error = %error, "async order gateway task failed");
            }
        }
        if let Some(task) = async_query_gateway_task {
            if let Err(error) = task.await {
                tracing::warn!(event = "async_order_query_gateway_task_failed", component = "execution", error = %error, "async order query gateway task failed");
            }
        }
        remove_socket(&socket_path)?;
        server.abort();
        let _ = server.await;
        info!(
            event = "process_stopped",
            component = "execution",
            "execution process stopped"
        );
        Ok(())
    }
}
