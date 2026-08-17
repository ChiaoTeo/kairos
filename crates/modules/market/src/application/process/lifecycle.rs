use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub(crate) struct MarketProcessSettings {
    pub(crate) publication_interval: Duration,
    pub(crate) freshness_check_interval: Duration,
    pub(crate) freshness_max_age: Duration,
    pub(crate) reference_recovery_interval: Duration,
    pub(crate) shutdown_timeout: Duration,
    pub(crate) publication_queue_capacity: usize,
}

pub(super) fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}

pub(super) fn remove_socket(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => std::fs::remove_file(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

use std::collections::BTreeMap;
use std::path::PathBuf;

use kairos_protocol::InstanceIdentity;
use serde_json::json;
use tokio::net::UnixListener;
use tokio::sync::{mpsc, mpsc::Receiver};
use tracing::info;

use super::actor_task::{log_event, MarketActorTask};
use super::publication::{MarketChangePublisher, MarketHistoryRecorder};
use crate::application::{MarketApplication, ReconcileMarketUniverse};
use crate::services::control::{spawn_server as spawn_control_server, EngineCommand};
use crate::services::publication::EventFanout;
use crate::services::publication::MarketEventEncoder;
use crate::services::source::SourceActivator;

pub struct MarketProcess {
    pub(super) actor_task: MarketActorTask,
    socket_path: PathBuf,
    event_socket_path: Option<PathBuf>,
    market_universe_updates: Option<Receiver<ReconcileMarketUniverse>>,
    publication_queue_capacity: usize,
    lifecycle_guard: Option<Box<dyn Send>>,
    event_publisher: Option<kairos_transport::AeronBytePublisher>,
}
impl MarketProcess {
    pub(crate) fn new_configured_with_activator<P: MarketChangePublisher + 'static>(
        application: MarketApplication,
        publisher: P,
        socket_path: impl Into<PathBuf>,
        event_socket_path: impl Into<PathBuf>,
        identity: InstanceIdentity,
        settings: MarketProcessSettings,
        source_activator: Option<Box<dyn SourceActivator>>,
        event_encoder: MarketEventEncoder,
    ) -> Result<Self, String> {
        for (name, value) in [
            ("publication interval", settings.publication_interval),
            (
                "freshness check interval",
                settings.freshness_check_interval,
            ),
            ("freshness max age", settings.freshness_max_age),
            (
                "reference recovery interval",
                settings.reference_recovery_interval,
            ),
            ("shutdown timeout", settings.shutdown_timeout),
        ] {
            if value.is_zero() {
                return Err(format!("market process {name} must be positive"));
            }
        }
        if settings.publication_queue_capacity == 0 {
            return Err("market publication queue capacity must be positive".into());
        }
        let event_actor_id = application.current_view().actor_id;
        Ok(Self {
            actor_task: MarketActorTask {
                application,
                publisher: Box::new(publisher),
                event_actor_id: event_actor_id.to_string(),
                event_identity: identity,
                event_encoder,
                publication_interval: settings.publication_interval,
                freshness_check_interval: settings.freshness_check_interval,
                freshness_max_age: settings.freshness_max_age,
                shutdown_timeout: settings.shutdown_timeout,
                stop_requested: false,
                command_results: BTreeMap::new(),
                source_activator,
                history_recorder: MarketHistoryRecorder::Noop,
            },
            socket_path: socket_path.into(),
            event_socket_path: Some(event_socket_path.into()),
            market_universe_updates: None,
            publication_queue_capacity: settings.publication_queue_capacity,
            lifecycle_guard: None,
            event_publisher: None,
        })
    }

    pub(crate) fn with_lifecycle_guard<T: Send + 'static>(mut self, guard: T) -> Self {
        self.lifecycle_guard = Some(Box::new(guard));
        self
    }

    pub fn with_aeron_event_publisher(
        mut self,
        publisher: kairos_transport::AeronBytePublisher,
    ) -> Self {
        self.event_publisher = Some(publisher);
        self
    }

    pub(crate) fn without_event_socket(mut self) -> Self {
        self.event_socket_path = None;
        self
    }

    pub fn with_market_universe_updates(
        mut self,
        updates: Receiver<ReconcileMarketUniverse>,
    ) -> Self {
        self.market_universe_updates = Some(updates);
        self
    }

    pub(crate) fn with_history_recorder(
        mut self,
        recorder: crate::services::publication::HistoryQueue,
    ) -> Self {
        self.actor_task.history_recorder = MarketHistoryRecorder::Queue(recorder);
        self
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error>> {
        let MarketProcess {
            actor_task,
            socket_path,
            event_socket_path,
            mut market_universe_updates,
            publication_queue_capacity,
            lifecycle_guard,
            event_publisher,
        } = self;
        let _lifecycle_guard = lifecycle_guard;
        remove_socket(&socket_path)?;
        if let Some(path) = event_socket_path.as_deref() {
            remove_socket(path)?;
        }
        if let Some(parent) = socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = event_socket_path.as_deref().and_then(Path::parent) {
            tokio::fs::create_dir_all(parent).await?;
        }
        let listener = UnixListener::bind(&socket_path)?;
        let event_listener = match event_socket_path.as_deref() {
            Some(path) => Some(UnixListener::bind(path)?),
            None => None,
        };
        let publication_interval = actor_task.publication_interval;
        let publication_shutdown_timeout = actor_task.shutdown_timeout;
        let (http_sender, control_receiver) = mpsc::channel(1_024);
        let (event_sender, mut event_receiver) =
            mpsc::channel::<Vec<u8>>(publication_queue_capacity);
        let actor_sender = http_sender.clone();
        let http_server = spawn_control_server(listener, http_sender);
        let mut actor_task = tokio::spawn(async move {
            actor_task
                .run_actor_loop(control_receiver, event_sender)
                .await
        });
        kairos_workspace::logging::record_gauge("kairos.process.ready", 1);
        info!(event = "process_starting", component = "market", socket = %socket_path.display(), event_socket = ?event_socket_path, publication_interval_ms = publication_interval.as_millis(), "market process starting");
        log_event(
            "info",
            "market process ready",
            json!({
                "socket": socket_path,
                "event_socket": event_socket_path,
                "publication_interval_ms": publication_interval.as_millis(),
            }),
        );
        info!(event = "process_ready", component = "market", socket = %socket_path.display(), "market control socket ready");
        let mut event_fanout = EventFanout::new(publication_queue_capacity);
        let event_publisher = event_publisher;
        let actor_result = loop {
            tokio::select! {
                accepted = async {
                    match event_listener.as_ref() {
                        Some(listener) => listener.accept().await.map(Some),
                        None => std::future::pending().await,
                    }
                } => {
                    let Some((stream, _)) = accepted? else { continue };
                    event_fanout.add_client(stream);
                }
                Some(payload) = event_receiver.recv() => {
                    if let Some(publisher) = event_publisher.as_ref() {
                        publisher.publish(&payload)?;
                    }
                    event_fanout.publish(payload);
                }
                update = async {
                    match market_universe_updates.as_mut() {
                        Some(updates) => updates.recv().await,
                        None => std::future::pending().await,
                    }
                } => {
                    match update {
                        Some(update) => actor_sender
                            .send(EngineCommand::ReconcileMarketUniverse(update))
                            .await
                            .map_err(|_| "market engine command queue is closed")?,
                        None => market_universe_updates = None,
                    }
                }
                result = &mut actor_task => {
                    break result;
                }
            }
        };
        while let Ok(payload) = event_receiver.try_recv() {
            if let Some(publisher) = event_publisher.as_ref() {
                publisher.publish(&payload)?;
            }
            event_fanout.publish(payload);
        }
        event_fanout.shutdown(publication_shutdown_timeout).await;
        remove_socket(&socket_path)?;
        if let Some(path) = event_socket_path.as_deref() {
            remove_socket(path)?;
        }
        info!(
            event = "process_stopped",
            component = "market",
            "market process stopped"
        );
        http_server.abort();
        let _ = http_server.await;
        actor_result
            .map_err(|error| std::io::Error::other(error.to_string()))?
            .map_err(std::io::Error::other)?;
        Ok(())
    }
}
