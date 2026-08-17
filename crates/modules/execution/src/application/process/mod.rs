use crate::application::{
    BacktestApplication, ExecutionApplication, RemoteOrderQuery, SubmitOrder,
};
use crate::services::actor::RemoteOrderEvent;
use crate::services::audit::ExecutionAudit;
use crate::services::control::{
    start as start_control_transport, ControlIngress, ControlOperation, ControlRequest,
    ControlResponse, RuntimeMetrics,
};
use crate::services::gateway::{
    AsyncQueuedOrderEntry, AsyncQueuedOrderQuery, QueuedOrderEntry, QueuedOrderQuery,
};
use crate::services::publication::{
    AeronExecutionEventPublisher, ExecutionEventPublication, SharedExecutionSnapshotPublisher,
    SharedIntentSnapshotPublisher,
};
use crate::services::simulation::{ExecutionSimulator, SimulatedAccountSettlement};
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, IntegrationError,
};
use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use tokio::net::UnixListener;
use tracing::info;
mod gateways;
mod ingress;
mod lifecycle;
mod publication;
mod readiness;
mod recovery;
mod simulation;
mod streams;

use gateways::{NoAsyncOrderEntryConnection, NoAsyncOrderEventSource, NoAsyncOrderQueryConnection};
use ingress::remote_order_event_from_envelope;
use lifecycle::now_unix_nanos;
use readiness::{
    process_readiness, route_status, set_route_readiness, ExecutionRouteReadiness,
    SharedRouteReadiness,
};
#[cfg(test)]
use recovery::{release_recovery_barrier, resync_required};
use recovery::{release_route_recovery_barrier, resync_targets};
pub(crate) use streams::ExecutionAsyncRoute;

pub(crate) struct ExecutionProcess<
    E = NoAsyncOrderEntryConnection,
    Q = NoAsyncOrderQueryConnection,
    S = NoAsyncOrderEventSource,
> {
    application: ExecutionApplication,
    simulator: Option<ExecutionSimulator>,
    socket_path: PathBuf,
    audit: Option<ExecutionAudit>,
    simulated_account_settlement: Option<SimulatedAccountSettlement>,
    stopping: bool,
    last_published_generation: Option<u64>,
    snapshot_publisher: Option<SharedExecutionSnapshotPublisher>,
    intent_snapshot_publisher: Option<SharedIntentSnapshotPublisher>,
    event_publisher: Option<ExecutionEventPublication>,
    metrics: std::sync::Arc<RuntimeMetrics>,
    last_remote_reconcile_unix_nanos: u64,
    async_order_entry: Option<E>,
    async_order_query: Option<Q>,
    async_execution_streams: Vec<ExecutionAsyncRoute<S>>,
    route_readiness: SharedRouteReadiness,
}

const EXCHANGE_BATCH_LIMIT: usize = 64;

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    fn state_loop(
        mut self,
        command_receiver: Receiver<ControlRequest>,
        query_receiver: Receiver<ControlRequest>,
        exchange_receiver: Receiver<RemoteOrderEvent>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.flush_events()?;
        self.publish_snapshots()?;
        while !self.stopping {
            let now = now_unix_nanos();
            let business_now = self
                .simulator
                .as_ref()
                .and_then(|simulator| simulator.business_time())
                .map(kairos_primitives::UnixNanos::get)
                .unwrap_or(now);
            let recovery_targets = resync_targets(&self.route_readiness);
            let recovery_required = !recovery_targets.is_empty();
            if self.application.has_order_query()
                && (recovery_required
                    || now.saturating_sub(self.last_remote_reconcile_unix_nanos) >= 5_000_000_000)
            {
                self.last_remote_reconcile_unix_nanos = now;
                let queries = if recovery_required {
                    recovery_targets
                } else {
                    vec![(usize::MAX, None)]
                };
                let mut total_changed = 0;
                for (route_index, binding_id) in queries {
                    match self.application.reconcile_remote_orders(RemoteOrderQuery {
                        binding_id: binding_id.clone(),
                        limit: Some(200),
                        ..RemoteOrderQuery::default()
                    }) {
                        Ok(changed) => {
                            total_changed += changed;
                            if route_index == usize::MAX {
                                self.application.complete_writer_reconciliation();
                            }
                            if route_index != usize::MAX {
                                release_route_recovery_barrier(&self.route_readiness, route_index);
                            }
                            tracing::info!(
                                event = "remote_order_reconciliation_completed",
                                component = "execution",
                                changed,
                                binding_id = ?binding_id,
                                recovery_barrier = route_index != usize::MAX,
                                "remote order reconciliation completed"
                            );
                        }
                        Err(error) => tracing::warn!(
                            event = "remote_order_reconciliation_failed",
                            component = "execution",
                            binding_id = ?binding_id,
                            error = %error,
                            "remote order reconciliation failed"
                        ),
                    }
                }
                if total_changed > 0 {
                    self.flush_events()?;
                    self.publish_snapshots()?;
                }
            }
            if self.application.refresh_maker_quotes()? > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            if self
                .application
                .advance_due_intent_orders(business_now, EXCHANGE_BATCH_LIMIT)?
                > 0
            {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            if self.application.expire_due_intents(business_now)? > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
            }
            let mut exchange_batch = 0;
            while exchange_batch < EXCHANGE_BATCH_LIMIT {
                match exchange_receiver.try_recv() {
                    Ok(event) => {
                        self.metrics
                            .pending_exchange_events
                            .fetch_sub(1, Ordering::Relaxed);
                        self.apply_exchange_event(event)?;
                        exchange_batch += 1;
                    }
                    Err(std::sync::mpsc::TryRecvError::Empty) => break,
                    Err(std::sync::mpsc::TryRecvError::Disconnected) => return Ok(()),
                }
            }
            if exchange_batch > 0 {
                self.flush_events()?;
                self.publish_snapshots()?;
                self.metrics
                    .exchange_batches
                    .fetch_add(1, Ordering::Relaxed);
                self.metrics
                    .max_exchange_batch
                    .fetch_max(exchange_batch, Ordering::Relaxed);
                if let Ok(request) = command_receiver.try_recv() {
                    self.handle_http_request(request)?;
                } else if let Ok(request) = query_receiver.try_recv() {
                    self.handle_http_request(request)?;
                }
                continue;
            }
            if let Ok(request) = command_receiver.try_recv() {
                self.metrics
                    .pending_commands
                    .fetch_sub(1, Ordering::Relaxed);
                self.handle_http_request(request)?;
                continue;
            }
            if let Ok(request) = query_receiver.try_recv() {
                self.metrics.pending_queries.fetch_sub(1, Ordering::Relaxed);
                self.handle_http_request(request)?;
                continue;
            }

            match exchange_receiver.recv_timeout(std::time::Duration::from_millis(10)) {
                Ok(event) => {
                    self.metrics
                        .pending_exchange_events
                        .fetch_sub(1, Ordering::Relaxed);
                    self.apply_exchange_event(event)?;
                    let mut batch = 1;
                    while batch < EXCHANGE_BATCH_LIMIT {
                        match exchange_receiver.try_recv() {
                            Ok(event) => {
                                self.metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                self.apply_exchange_event(event)?;
                                batch += 1;
                            }
                            Err(std::sync::mpsc::TryRecvError::Empty)
                            | Err(std::sync::mpsc::TryRecvError::Disconnected) => break,
                        }
                    }
                    self.flush_events()?;
                    self.publish_snapshots()?;
                    self.metrics
                        .exchange_batches
                        .fetch_add(1, Ordering::Relaxed);
                    self.metrics
                        .max_exchange_batch
                        .fetch_max(batch, Ordering::Relaxed);
                }
                Err(RecvTimeoutError::Disconnected) => break,
                Err(RecvTimeoutError::Timeout) => {}
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
