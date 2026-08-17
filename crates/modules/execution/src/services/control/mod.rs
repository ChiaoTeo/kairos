//! Private HTTP-over-Unix-socket transport for the Execution control plane.
//!
//! This adapter owns raw HTTP request parsing, bounded body reads, ingress
//! classification and HTTP response encoding. The application process only
//! receives an internal mailbox request.

use std::sync::atomic::{AtomicU64, AtomicUsize};
use std::sync::mpsc::SyncSender;
use tokio::sync::oneshot;

mod response;
mod transport;
mod wire;

pub(crate) use response::ControlResponse;
pub(crate) use transport::start;
#[cfg(test)]
pub(crate) use wire::{request_class, v2_submit_intent};

use crate::application::{
    BacktestRequest, CancelIntent, CancelOrder, ExecuteStrategyIntent, ExecutionFillReport,
    ExecutionOrderOptions, ExecutionRouteQuery, ExpireIntent, MarketObservation,
    RefreshQuoteIntent, RemoteOrderQuery, SubmitOrder,
};
use kairos_primitives::{OrderId, Price, Quantity};

pub(crate) struct ReplaceOrderPatch {
    pub(crate) quantity: Option<Quantity>,
    pub(crate) limit_price: Option<Option<Price>>,
    pub(crate) options: ExecutionOrderOptions,
}

pub(crate) enum ControlOperation {
    Health,
    AvailableRoutes(ExecutionRouteQuery),
    AdvanceTime(u64),
    SubmitIntent {
        intent: ExecuteStrategyIntent,
        idempotency_key: String,
    },
    SubmitOrder(SubmitOrder),
    CancelOrder(CancelOrder),
    ReplaceOrder {
        order_id: OrderId,
        patch: ReplaceOrderPatch,
    },
    Reconcile(RemoteOrderQuery),
    LinkUnknownRemote {
        remote_order_id: String,
        local_order_id: String,
    },
    EvaluateBacktest(BacktestRequest),
    RunBacktest(BacktestRequest),
    ApplyBacktestMarket(MarketObservation),
    CancelIntent(CancelIntent),
    ExpireIntent(ExpireIntent),
    RefreshQuote(RefreshQuoteIntent),
    PreviewSubmit(SubmitOrder),
    RecordFill(ExecutionFillReport),
    Stop,
}

impl ControlOperation {
    fn class(&self) -> RequestClass {
        match self {
            Self::Health | Self::AvailableRoutes(_) => RequestClass::Query,
            _ => RequestClass::Command,
        }
    }
}

pub(crate) struct ControlRequest {
    pub(crate) operation: ControlOperation,
    pub(crate) response: oneshot::Sender<Result<ControlResponse, String>>,
}

#[derive(Default)]
pub(crate) struct RuntimeMetrics {
    pub(crate) pending_commands: AtomicUsize,
    pub(crate) pending_queries: AtomicUsize,
    pub(crate) pending_exchange_events: AtomicUsize,
    pub(crate) exchange_events_applied: AtomicU64,
    pub(crate) exchange_batches: AtomicU64,
    pub(crate) max_exchange_batch: AtomicUsize,
    pub(crate) state_loop_errors: AtomicU64,
    pub(crate) last_operation_micros: AtomicU64,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) enum RequestClass {
    Command,
    Query,
}

#[derive(Clone)]
pub(crate) struct ControlIngress {
    command_tx: SyncSender<ControlRequest>,
    query_tx: SyncSender<ControlRequest>,
    metrics: std::sync::Arc<RuntimeMetrics>,
}

impl ControlIngress {
    pub(crate) fn new(
        command_tx: SyncSender<ControlRequest>,
        query_tx: SyncSender<ControlRequest>,
        metrics: std::sync::Arc<RuntimeMetrics>,
    ) -> Self {
        Self {
            command_tx,
            query_tx,
            metrics,
        }
    }
}
