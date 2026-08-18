//! Dedicated order-entry gateway worker.
//!
//! The worker owns the concrete provider connection. ExecutionApplication
//! sees only a provider-neutral proxy, so synchronous SDK/network work does
//! not run on the UDS/Tokio runtime thread.

use kairos_integration::blocking::{
    OrderCommand as BlockingOrderCommand, OrderQuery as BlockingOrderQuery,
};
use kairos_integration::{
    CommandOutcome, ExternalOrder, ExternalOrderQuery, IntegrationError, OrderCommand,
    OrderEntryEvent, OrderEntryRequest, OrderQuery,
};
use std::sync::mpsc::{Receiver, SyncSender};
use std::time::Duration;

mod entry;
mod fence;
mod managed;
mod query;

pub use entry::AsyncQueuedOrderEntry;
pub use fence::ExecutionWriterFence;
pub(crate) use managed::{build_managed_gateways, ExecutionConnectionPlan};
pub use query::AsyncQueuedOrderQuery;

#[cfg(test)]
mod tests {
    use super::*;
    use kairos_integration::{
        DecimalValue, OrderEntryOptions, OrderEntryStatus, OrderSide, OrderType,
        ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    };
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    struct AwaitingOrderEntry {
        used_caller_runtime: Arc<AtomicBool>,
        started: Arc<tokio::sync::Notify>,
        release: Arc<tokio::sync::Notify>,
    }

    impl OrderCommand for AwaitingOrderEntry {
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

    impl OrderQuery for RuntimeCheckingQuery {
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

    fn request() -> OrderEntryRequest {
        OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new("order-async-gateway").unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-usdt").unwrap(),
            market_id: None,
            participant_instrument: ParticipantInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
                Some(ParticipantInstrumentTypeRef::new("binance-spot").unwrap()),
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
}
