//! Dedicated order-entry gateway worker.
//!
//! The worker owns the concrete provider connection. ExecutionApplication
//! sees only a provider-neutral proxy, so synchronous SDK/network work does
//! not run on the UDS/Tokio runtime thread.

use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ConnectionHealth, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderEntryEvent, OrderEntryRequest,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::time::Duration;

mod entry;
mod events;
mod query;

pub use entry::{AsyncQueuedOrderEntry, QueuedOrderEntry};
pub use events::AsyncQueuedOrderEventSource;
pub use query::{AsyncQueuedOrderQuery, QueuedOrderQuery};

#[cfg(test)]
mod tests {
    use super::*;
    use kairos_integration::application::{
        ConnectionLifecycle, DecimalValue, OrderEntryOptions, OrderEntryStatus, OrderSide,
        OrderType, ParticipantKind, ParticipantRef, ProviderInstrumentRef,
    };
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    struct AwaitingOrderEntry {
        used_caller_runtime: Arc<AtomicBool>,
        started: Arc<tokio::sync::Notify>,
        release: Arc<tokio::sync::Notify>,
    }

    impl AsyncOrderEntryConnection for AwaitingOrderEntry {
        async fn submit_order(
            &mut self,
            request: &OrderEntryRequest,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            self.used_caller_runtime.store(
                tokio::runtime::Handle::try_current().is_ok(),
                Ordering::Release,
            );
            self.started.notify_one();
            self.release.notified().await;
            Ok(CommandOutcome::Confirmed(OrderEntryEvent {
                order_id: request.order_id.clone(),
                status: OrderEntryStatus::Accepted,
                remote_order_id: None,
                filled_quantity: None,
                occurred_at_unix_nanos: 1.into(),
                reason: "accepted".into(),
            }))
        }

        async fn cancel_order(
            &mut self,
            _request: &OrderEntryRequest,
            _remote_order_id: &str,
            _at_unix_nanos: u64,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            Err(IntegrationError::UnsupportedOperation)
        }
    }

    struct RuntimeCheckingQuery(Arc<AtomicBool>);

    impl AsyncOrderQueryConnection for RuntimeCheckingQuery {
        async fn open_orders(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            self.0.store(
                tokio::runtime::Handle::try_current().is_ok(),
                Ordering::Release,
            );
            Ok(Vec::new())
        }

        async fn order_history(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            Ok(Vec::new())
        }

        async fn order_detail(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError> {
            Ok(None)
        }
    }

    struct PendingEventSource {
        lifecycle: ConnectionLifecycle,
    }

    impl AsyncOrderEventSource for PendingEventSource {
        async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            self.lifecycle = ConnectionLifecycle::Ready;
            Ok(())
        }

        async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.lifecycle = ConnectionLifecycle::Stopped;
            Ok(())
        }

        fn channel_health(&self) -> ConnectionHealth {
            ConnectionHealth {
                lifecycle: self.lifecycle,
                healthy: self.lifecycle == ConnectionLifecycle::Ready,
                authenticated: self.lifecycle == ConnectionLifecycle::Ready,
                last_error: None,
            }
        }

        async fn next_order_event(
            &mut self,
        ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
            std::future::pending().await
        }
    }

    fn request() -> OrderEntryRequest {
        OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new("order-async-gateway").unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-usdt").unwrap(),
            market_id: None,
            provider_instrument: ProviderInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
                Some(kairos_integration::participants::binance::ConnectionDomain::Spot.into()),
                "BTCUSDT",
            )
            .unwrap(),
            side: OrderSide::Buy,
            quantity: DecimalValue::new(1, 0),
            order_type: OrderType::Market,
            limit_price: None,
            options: OrderEntryOptions::default(),
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn async_command_uses_business_runtime_and_finishes_after_shutdown_starts() {
        let used_caller_runtime = Arc::new(AtomicBool::new(false));
        let started = Arc::new(tokio::sync::Notify::new());
        let release = Arc::new(tokio::sync::Notify::new());
        let (mut proxy, worker) = AsyncQueuedOrderEntry::channel(
            AwaitingOrderEntry {
                used_caller_runtime: Arc::clone(&used_caller_runtime),
                started: Arc::clone(&started),
                release: Arc::clone(&release),
            },
            2,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call = tokio::task::spawn_blocking(move || proxy.submit_order(&request()));

        started.notified().await;
        shutdown.send(true).unwrap();
        release.notify_one();

        let result = call.await.unwrap().unwrap();
        assert!(matches!(result, CommandOutcome::Confirmed(_)));
        assert!(used_caller_runtime.load(Ordering::Acquire));
        worker.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn async_query_uses_business_runtime_without_a_dedicated_thread_runtime() {
        let used_caller_runtime = Arc::new(AtomicBool::new(false));
        let (mut proxy, worker) = AsyncQueuedOrderQuery::channel(
            RuntimeCheckingQuery(Arc::clone(&used_caller_runtime)),
            2,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call =
            tokio::task::spawn_blocking(move || proxy.open_orders(&ExternalOrderQuery::default()));

        assert!(call.await.unwrap().unwrap().is_empty());
        assert!(used_caller_runtime.load(Ordering::Acquire));
        let _ = shutdown.send(true);
        worker.await.unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn async_event_proxy_bounds_one_shot_cli_poll() {
        let (mut proxy, worker) = AsyncQueuedOrderEventSource::channel(
            PendingEventSource {
                lifecycle: ConnectionLifecycle::Created,
            },
            1,
        );
        let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
        let worker = tokio::spawn(worker.run(shutdown_rx));
        let call = tokio::task::spawn_blocking(move || proxy.try_next_order_event());

        assert!(call.await.unwrap().unwrap().is_none());
        let _ = shutdown.send(true);
        worker.await.unwrap();
    }
}
