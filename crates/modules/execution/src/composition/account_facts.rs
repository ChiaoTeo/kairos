use crate::application::ExecutionAccountFacts;
use crate::domain::{
    ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderCommitment, OrderSide,
};
use kairos_account_contract::{
    AccountContractClient, DecimalValue, Fill, OrderEvent, SimulatedFill,
};
use rust_decimal::Decimal;
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread::JoinHandle;
use std::time::Duration;

enum AccountFactRequest {
    Order {
        order: ExecutionOrder,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Fill {
        fill: ExecutionFill,
        order: ExecutionOrder,
        commitment: OrderCommitment,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
}

/// Bounded worker boundary that keeps Account control I/O off the Execution
/// state thread while preserving an explicit delivery result for the caller.
pub struct QueuedExecutionAccountFacts {
    sender: std::sync::mpsc::SyncSender<AccountFactRequest>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl QueuedExecutionAccountFacts {
    pub fn start(inner: Box<dyn ExecutionAccountFacts>, capacity: usize) -> Result<Self, String> {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker = std::thread::Builder::new()
            .name("execution-account-facts".into())
            .spawn(move || Self::run(inner, receiver, worker_stop))
            .map_err(|error| format!("start Execution Account fact worker: {error}"))?;
        Ok(Self {
            sender,
            stop,
            worker: Some(worker),
        })
    }

    fn run(
        mut inner: Box<dyn ExecutionAccountFacts>,
        receiver: std::sync::mpsc::Receiver<AccountFactRequest>,
        stop: Arc<AtomicBool>,
    ) {
        while !stop.load(Ordering::Acquire) {
            let request = match receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(request) => request,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            };
            match request {
                AccountFactRequest::Order { order, reply } => {
                    let _ = reply.send(inner.publish_order(&order));
                }
                AccountFactRequest::Fill {
                    fill,
                    order,
                    commitment,
                    reply,
                } => {
                    let _ = reply.send(inner.publish_fill(&fill, &order, &commitment));
                }
            }
        }
    }

    fn request(
        &self,
        request: AccountFactRequest,
        reply: std::sync::mpsc::Receiver<Result<(), String>>,
    ) -> Result<(), String> {
        self.sender
            .try_send(request)
            .map_err(|error| format!("Execution Account fact queue unavailable: {error}"))?;
        reply
            .recv_timeout(Duration::from_secs(5))
            .map_err(|error| format!("Execution Account fact worker unavailable: {error}"))?
    }
}

impl ExecutionAccountFacts for QueuedExecutionAccountFacts {
    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            AccountFactRequest::Order {
                order: order.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }

    fn publish_fill(
        &mut self,
        fill: &ExecutionFill,
        order: &ExecutionOrder,
        commitment: &OrderCommitment,
    ) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            AccountFactRequest::Fill {
                fill: fill.clone(),
                order: order.clone(),
                commitment: commitment.clone(),
                reply: reply_tx,
            },
            reply_rx,
        )
    }
}

impl Drop for QueuedExecutionAccountFacts {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

/// Concrete outbound adapter for Account-owned observed order and settlement
/// facts. It deliberately contains no admission, planning, quote, or Risk
/// reservation behavior.
pub struct SocketExecutionAccountFacts {
    accounts: BTreeMap<String, PathBuf>,
    clients: BTreeMap<String, AccountContractClient>,
    simulated_settlement: bool,
}

impl SocketExecutionAccountFacts {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        let value: Value = serde_json::from_slice(
            &std::fs::read(path.as_ref())
                .map_err(|error| format!("read endpoint manifest: {error}"))?,
        )
        .map_err(|error| format!("decode endpoint manifest: {error}"))?;
        let accounts = value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| "endpoint manifest has no accounts".to_string())?
            .iter()
            .map(|(account_id, endpoint)| {
                let socket = endpoint
                    .get("socket")
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("account {account_id} has no socket"))?;
                Ok((account_id.clone(), PathBuf::from(socket)))
            })
            .collect::<Result<_, String>>()?;
        Ok(Self {
            accounts,
            clients: BTreeMap::new(),
            simulated_settlement: false,
        })
    }

    pub fn with_simulated_settlement(mut self, enabled: bool) -> Self {
        self.simulated_settlement = enabled;
        self
    }

    fn client(&mut self, account_id: &str) -> Result<&AccountContractClient, String> {
        if !self.clients.contains_key(account_id) {
            let socket = self
                .accounts
                .get(account_id)
                .ok_or_else(|| format!("account is not bound: {account_id}"))?;
            let client =
                AccountContractClient::connect(socket).map_err(|error| error.to_string())?;
            self.clients.insert(account_id.to_owned(), client);
        }
        self.clients
            .get(account_id)
            .ok_or_else(|| format!("account client is unavailable: {account_id}"))
    }
}

impl ExecutionAccountFacts for SocketExecutionAccountFacts {
    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        self.client(order.account_id.as_str())?
            .publish_order_event(&OrderEvent {
                order_id: order.order_id.to_string(),
                status: account_order_status(order.status).into(),
                remote_order_id: order.remote_order_id.as_ref().map(ToString::to_string),
                filled_quantity: decimal_value(
                    order.filled_quantity.mantissa(),
                    order.filled_quantity.scale(),
                ),
                occurred_at_unix_nanos: order.updated_at_unix_nanos.get(),
                reason: order.reason.clone(),
            })
            .map_err(|error| error.to_string())
    }

    fn publish_fill(
        &mut self,
        fill: &ExecutionFill,
        order: &ExecutionOrder,
        commitment: &OrderCommitment,
    ) -> Result<(), String> {
        if order.order_id != fill.order_id {
            return Err("fill/order identity mismatch".into());
        }
        let account_id = order.account_id.to_string();
        let side = match fill.side {
            OrderSide::Buy => "buy",
            OrderSide::Sell => "sell",
        };
        if self.simulated_settlement {
            let notional = Decimal::try_new(fill.quantity.mantissa(), fill.quantity.scale().into())
                .ok()
                .and_then(|quantity| {
                    Decimal::try_new(fill.price.mantissa(), fill.price.scale().into())
                        .ok()
                        .and_then(|price| quantity.checked_mul(price))
                })
                .ok_or_else(|| "simulated settlement notional overflow".to_string())?;
            let settlement_delta = if fill.side == OrderSide::Buy {
                -notional
            } else {
                notional
            }
            .normalize();
            return self
                .client(&account_id)?
                .publish_simulated_fill(&SimulatedFill {
                    fill_id: fill.fill_id.to_string(),
                    order_id: fill.order_id.to_string(),
                    segment_key: order.segment_key.to_string(),
                    instrument_id: fill.instrument_id.to_string(),
                    quantity: decimal_value(fill.quantity.mantissa(), fill.quantity.scale()),
                    price: decimal_value(fill.price.mantissa(), fill.price.scale()),
                    side: side.into(),
                    settlement_asset: commitment
                        .settlement_asset
                        .as_ref()
                        .ok_or_else(|| {
                            "simulated fill has no Reference-confirmed settlement asset".to_string()
                        })?
                        .to_string(),
                    settlement_delta: DecimalValue {
                        mantissa: i64::try_from(settlement_delta.mantissa())
                            .map_err(|_| "settlement delta exceeds Decimal64 range")?,
                        scale: settlement_delta.scale() as u8,
                    },
                    fee_asset: fill.fee_currency.as_ref().map(ToString::to_string),
                    fee_amount: (fill.fee.mantissa() != 0)
                        .then_some(decimal_value(fill.fee.mantissa(), fill.fee.scale())),
                    occurred_at_unix_nanos: fill.occurred_at_unix_nanos.get(),
                })
                .map_err(|error| error.to_string());
        }
        self.client(&account_id)?
            .publish_fill(&Fill {
                fill_id: fill.fill_id.to_string(),
                order_id: fill.order_id.to_string(),
                segment_key: order.segment_key.to_string(),
                instrument_id: fill.instrument_id.to_string(),
                quantity: decimal_value(fill.quantity.mantissa(), fill.quantity.scale()),
                price: decimal_value(fill.price.mantissa(), fill.price.scale()),
                side: side.into(),
                occurred_at_unix_nanos: fill.occurred_at_unix_nanos.get(),
                fee_asset: fill.fee_currency.as_ref().map(ToString::to_string),
                fee_amount: Some(decimal_value(fill.fee.mantissa(), fill.fee.scale())),
            })
            .map_err(|error| error.to_string())
    }
}

fn decimal_value(mantissa: i64, scale: u8) -> DecimalValue {
    DecimalValue { mantissa, scale }
}

fn account_order_status(status: ExecutionOrderStatus) -> &'static str {
    match status {
        ExecutionOrderStatus::Pending => "Planned",
        ExecutionOrderStatus::Submitting => "Submitting",
        ExecutionOrderStatus::Accepted => "Acknowledged",
        ExecutionOrderStatus::PartiallyFilled => "PartiallyFilled",
        ExecutionOrderStatus::Filled => "Filled",
        ExecutionOrderStatus::CancelRequested => "CancelRequested",
        ExecutionOrderStatus::Canceled => "Canceled",
        ExecutionOrderStatus::Rejected => "Rejected",
        ExecutionOrderStatus::Expired => "Expired",
        ExecutionOrderStatus::Unknown | ExecutionOrderStatus::Failed => "Unknown",
    }
}
