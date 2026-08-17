//! Execution-owned runtime policy for Integration order-event sources.

use super::*;

/// One configured Integration order-event source and its business readiness policy.
/// The provider connection remains Integration-owned; required/optional
/// classification and binding identity belong to Execution.
pub struct ExecutionAsyncRoute<S> {
    pub route_id: String,
    pub required: bool,
    pub binding_id: Option<String>,
    pub(super) source: S,
}

impl<S> ExecutionAsyncRoute<S> {
    pub(crate) fn new(route_id: impl Into<String>, required: bool, source: S) -> Self {
        Self {
            route_id: route_id.into(),
            required,
            binding_id: None,
            source,
        }
    }

    pub(crate) fn with_binding_id(mut self, binding_id: impl Into<String>) -> Self {
        self.binding_id = Some(binding_id.into());
        self
    }

    pub(crate) fn into_source(self) -> S {
        self.source
    }
}

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(super) fn start_async_stream_consumers(
        &mut self,
        sender: SyncSender<RemoteOrderEvent>,
        shutdown: tokio::sync::watch::Receiver<bool>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Vec<tokio::task::JoinHandle<()>>
    where
        S: AsyncOrderEventSource + 'static,
    {
        let route_readiness = std::sync::Arc::clone(&self.route_readiness);
        self.async_execution_streams
            .drain(..)
            .enumerate()
            .map(|(route_index, mut route)| {
                let sender = sender.clone();
                let metrics = std::sync::Arc::clone(&metrics);
                let mut shutdown = shutdown.clone();
                let readiness = std::sync::Arc::clone(&route_readiness);
                let route_id = route.route_id.clone();
                tokio::spawn(async move {
            'stream: loop {
                if matches!(
                    route_status(&readiness, route_index),
                    Some("resync_required" | "recovering")
                ) {
                    let _ = route.source.disconnect_channel().await;
                    if route_status(&readiness, route_index) == Some("resync_required") {
                        loop {
                            if route_status(&readiness, route_index) == Some("recovering") {
                                break;
                            }
                            tokio::select! {
                                changed = shutdown.changed() => {
                                    if changed.is_err() || *shutdown.borrow() {
                                        break 'stream;
                                    }
                                }
                                _ = tokio::time::sleep(std::time::Duration::from_millis(50)) => {}
                            }
                        }
                    }
                    let reconnect = tokio::select! {
                        biased;
                        changed = shutdown.changed() => {
                            if changed.is_err() || *shutdown.borrow() {
                                break 'stream;
                            }
                            continue 'stream;
                        }
                        result = route.source.reconnect_channel() => result,
                    };
                    match reconnect {
                        Ok(()) => {
                            kairos_workspace::logging::record_counter("kairos.reconnect", 1);
                            set_route_readiness(&readiness, route_index, "ready", None);
                        }
                        Err(error) => {
                            set_route_readiness(
                                &readiness,
                                route_index,
                                "degraded",
                                Some(error.to_string()),
                            );
                            tracing::warn!(event = "async_exchange_stream_recovery_failed", component = "execution", route_id = %route_id, error = %error, "execution stream failed after reconciliation barrier");
                            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
                        }
                    }
                    continue;
                }
                let result = tokio::select! {
                    biased;
                    changed = shutdown.changed() => {
                        if changed.is_err() || *shutdown.borrow() {
                            break;
                        }
                        continue;
                    }
                    result = route.source.next_order_event() => result,
                };
                match result {
                    Ok(envelope) => {
                        let message = remote_order_event_from_envelope(envelope);
                        metrics
                            .pending_exchange_events
                            .fetch_add(1, Ordering::Relaxed);
                        match sender.try_send(message) {
                            Ok(()) => {}
                            Err(std::sync::mpsc::TrySendError::Full(_)) => {
                                metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                metrics.state_loop_errors.fetch_add(1, Ordering::Relaxed);
                                tracing::warn!(
                                    event = "exchange_event_mailbox_overflow",
                                    component = "execution",
                                    route_id = %route_id,
                                    "execution event mailbox overflowed; stop stream and reconcile"
                                );
                                set_route_readiness(
                                    &readiness,
                                    route_index,
                                    "resync_required",
                                    Some("execution event mailbox overflowed".into()),
                                );
                                continue;
                            }
                            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                                metrics
                                    .pending_exchange_events
                                    .fetch_sub(1, Ordering::Relaxed);
                                break;
                            }
                        }
                    }
                    Err(error) => {
                        if matches!(
                            error,
                            IntegrationError::ResyncRequired(_)
                                | IntegrationError::Backpressure(_)
                        ) {
                            set_route_readiness(
                                &readiness,
                                route_index,
                                "resync_required",
                                Some(error.to_string()),
                            );
                            continue;
                        }
                        set_route_readiness(
                            &readiness,
                            route_index,
                            "degraded",
                            Some(error.to_string()),
                        );
                        tracing::warn!(event = "async_exchange_stream_error", component = "execution", route_id = %route_id, error = %error, "async exchange stream read failed");
                        let reconnect = tokio::select! {
                            biased;
                            changed = shutdown.changed() => {
                                if changed.is_err() || *shutdown.borrow() {
                                    break;
                                }
                                continue;
                            }
                            result = route.source.reconnect_channel() => result,
                        };
                        match reconnect {
                            Ok(()) => {
                                kairos_workspace::logging::record_counter("kairos.reconnect", 1);
                                set_route_readiness(&readiness, route_index, "ready", None);
                            }
                            Err(reconnect_error) => {
                                kairos_workspace::logging::record_counter("kairos.retry", 1);
                                tracing::warn!(event = "async_exchange_stream_reconnect_failed", component = "execution", error = %reconnect_error, "async exchange stream reconnect failed");
                            }
                        }
                        tokio::select! {
                            _ = tokio::time::sleep(std::time::Duration::from_millis(250)) => {}
                            changed = shutdown.changed() => {
                                if changed.is_err() || *shutdown.borrow() {
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            let _ = route.source.disconnect_channel().await;
                })
            })
            .collect()
    }

    pub(super) async fn connect_async_execution_streams(&mut self) -> Result<(), IntegrationError>
    where
        S: AsyncOrderEventSource,
    {
        for index in 0..self.async_execution_streams.len() {
            if let Err(error) = self.async_execution_streams[index]
                .source
                .connect_channel()
                .await
            {
                set_route_readiness(
                    &self.route_readiness,
                    index,
                    "degraded",
                    Some(error.to_string()),
                );
                if !self.async_execution_streams[index].required {
                    continue;
                }
                for connected in &mut self.async_execution_streams[..index] {
                    let _ = connected.source.disconnect_channel().await;
                }
                return Err(error);
            }
            let health = self.async_execution_streams[index].source.channel_health();
            if !health.healthy || !health.authenticated {
                set_route_readiness(&self.route_readiness, index, "degraded", health.last_error);
                if !self.async_execution_streams[index].required {
                    continue;
                }
                for connected in &mut self.async_execution_streams[..=index] {
                    let _ = connected.source.disconnect_channel().await;
                }
                return Err(IntegrationError::NotReady);
            }
            set_route_readiness(&self.route_readiness, index, "ready", None);
        }
        Ok(())
    }

    pub(super) fn start_stream_consumer(
        &mut self,
        sender: SyncSender<RemoteOrderEvent>,
        stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Option<std::thread::JoinHandle<()>> {
        let mut stream = self.application.take_execution_stream()?;
        Some(std::thread::spawn(move || {
            use std::sync::atomic::Ordering;
            use std::time::Duration;
            loop {
                if stop.load(Ordering::Acquire) {
                    break;
                }
                match stream.try_next_order_event() {
                    Ok(Some(envelope)) => {
                        let message = remote_order_event_from_envelope(envelope);
                        metrics
                            .pending_exchange_events
                            .fetch_add(1, Ordering::Relaxed);
                        if sender.send(message).is_err() {
                            metrics
                                .pending_exchange_events
                                .fetch_sub(1, Ordering::Relaxed);
                            break;
                        }
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                    Err(error) => {
                        tracing::warn!(event = "exchange_stream_error", component = "execution", error = %error, "exchange stream read failed");
                        if let Err(reconnect_error) = stream.reconnect_channel() {
                            kairos_workspace::logging::record_counter("kairos.retry", 1);
                            tracing::warn!(
                                event = "exchange_stream_reconnect_failed",
                                component = "execution",
                                error = %reconnect_error,
                                "exchange stream reconnect failed"
                            );
                        } else {
                            kairos_workspace::logging::record_counter("kairos.reconnect", 1);
                            tracing::info!(
                                event = "exchange_stream_reconnected",
                                component = "execution",
                                "exchange stream reconnected"
                            );
                        }
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
            }
            let _ = stream.disconnect_channel();
        }))
    }
}
