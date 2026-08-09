//! Runtime intent planning and safety checks.
//!
//! Execution owns the plan and lifecycle, while this composition adapter
//! talks to the already-running Account/Risk/Market processes through their
//! Unix sockets.  No business state is cached here.

use crate::application::{
    DependencyWatermarks, ExecuteStrategyIntent, ExecutionPreflight, SnapshotWatermark, SubmitOrder,
};
use crate::domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, RwLock,
};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use kairos_account_contract::client::{
    AccountContractClient, BalancesResponse, Capability, DecimalValue as AccountDecimal, Health,
    PositionsResponse,
};
use kairos_reference_contract::query::{ReferenceMarket, ReferenceQueryClient};
use kairos_risk_contract::client::RiskContractClient;
use kairos_risk_contract::client::{Amount as RiskAmount, Assessment, ReserveRequest, Usage};

const PROJECTION_REFRESH: Duration = Duration::from_millis(250);
const PROJECTION_MAX_AGE: Duration = Duration::from_secs(5);

#[derive(Clone)]
struct AccountProjection {
    health: Health,
    capabilities: Vec<Capability>,
    balances: BalancesResponse,
    positions: PositionsResponse,
    refreshed_at: Instant,
}

#[derive(Clone)]
struct MarketProjection {
    snapshot: kairos_market_contract::snapshot::MarketSnapshotRead,
    refreshed_at: Instant,
}

#[derive(Clone)]
struct ReferenceProjection {
    health: kairos_reference_contract::query::Health,
    markets: Vec<ReferenceMarket>,
    refreshed_at: Instant,
}

#[derive(Clone)]
struct RiskProjection {
    health: kairos_risk_contract::client::Health,
    refreshed_at: Instant,
}

#[derive(Default)]
struct DependencyProjection {
    accounts: BTreeMap<String, AccountProjection>,
    market: Option<MarketProjection>,
    reference: Option<ReferenceProjection>,
    risk: Option<RiskProjection>,
}

pub struct SocketExecutionPreflight {
    accounts: BTreeMap<String, PathBuf>,
    market_snapshot: Option<PathBuf>,
    reference: Option<PathBuf>,
    risk: Option<PathBuf>,
    reservations: BTreeMap<String, String>,
    reservation_amounts: BTreeMap<String, i64>,
    reservation_quantities: BTreeMap<String, i64>,
    orders: BTreeMap<String, (String, String)>,
    dependency_watermarks: DependencyWatermarks,
    projection: Arc<RwLock<DependencyProjection>>,
    projection_stop: Arc<AtomicBool>,
    projection_workers: Vec<JoinHandle<()>>,
    account_clients: BTreeMap<String, AccountContractClient>,
    risk_client: Option<RiskContractClient>,
}

const MAX_PRICE_DEVIATION_BPS: i64 = 500;

impl SocketExecutionPreflight {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        let manifest_path = path.as_ref().to_path_buf();
        let value: Value = serde_json::from_slice(
            &std::fs::read(&manifest_path)
                .map_err(|error| format!("read endpoint manifest: {error}"))?,
        )
        .map_err(|error| format!("decode endpoint manifest: {error}"))?;
        let mut accounts = BTreeMap::new();
        for (account_id, endpoint) in value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| "endpoint manifest has no accounts".to_string())?
        {
            let socket = endpoint
                .get("socket")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("account {account_id} has no socket"))?;
            accounts.insert(account_id.clone(), PathBuf::from(socket));
        }
        let components = value.get("components").and_then(Value::as_object);
        let endpoint = |name: &str| {
            components
                .and_then(|items| items.get(name))
                .and_then(|item| item.get("socket"))
                .and_then(Value::as_str)
                .map(PathBuf::from)
        };
        let instance_root = manifest_path
            .parent()
            .and_then(Path::parent)
            .map(Path::to_path_buf);
        let market_snapshot = instance_root.map(|root| {
            root.join("snapshots")
                .join("market")
                .join("market.snapshot")
        });
        let projection = Arc::new(RwLock::new(DependencyProjection::default()));
        let projection_stop = Arc::new(AtomicBool::new(false));
        let projection_workers = Self::start_projection_workers(
            accounts.clone(),
            market_snapshot.clone(),
            endpoint("reference"),
            endpoint("risk"),
            Arc::clone(&projection),
            Arc::clone(&projection_stop),
        );
        Ok(Self {
            accounts,
            market_snapshot,
            reference: endpoint("reference"),
            risk: endpoint("risk"),
            reservations: BTreeMap::new(),
            reservation_amounts: BTreeMap::new(),
            reservation_quantities: BTreeMap::new(),
            orders: BTreeMap::new(),
            dependency_watermarks: DependencyWatermarks::default(),
            projection,
            projection_stop,
            projection_workers,
            account_clients: BTreeMap::new(),
            risk_client: None,
        })
    }

    fn account_client(&mut self, account_id: &str) -> Result<&AccountContractClient, String> {
        if !self.account_clients.contains_key(account_id) {
            let socket = self
                .accounts
                .get(account_id)
                .ok_or_else(|| format!("account is not bound: {account_id}"))?
                .clone();
            let client =
                AccountContractClient::connect(socket).map_err(|error| error.to_string())?;
            self.account_clients.insert(account_id.to_owned(), client);
        }
        self.account_clients
            .get(account_id)
            .ok_or_else(|| format!("account client is unavailable: {account_id}"))
    }

    fn risk_client(&mut self) -> Result<&RiskContractClient, String> {
        if self.risk_client.is_none() {
            let socket = self
                .risk
                .as_ref()
                .ok_or_else(|| "risk endpoint is not configured".to_string())?
                .clone();
            self.risk_client =
                Some(RiskContractClient::connect(socket).map_err(|error| error.to_string())?);
        }
        self.risk_client
            .as_ref()
            .ok_or_else(|| "risk client is unavailable".to_string())
    }

    fn start_projection_workers(
        accounts: BTreeMap<String, PathBuf>,
        market_snapshot: Option<PathBuf>,
        reference: Option<PathBuf>,
        risk: Option<PathBuf>,
        projection: Arc<RwLock<DependencyProjection>>,
        stop: Arc<AtomicBool>,
    ) -> Vec<JoinHandle<()>> {
        let mut workers = Vec::new();
        for (account_id, socket) in accounts {
            let projection = Arc::clone(&projection);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let client = loop {
                    match AccountContractClient::connect(&socket) {
                        Ok(client) => break client,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(PROJECTION_REFRESH)
                        }
                        Err(_) => return,
                    }
                };
                let mut last_watermark = None;
                while !stop.load(Ordering::Acquire) {
                    let result = (|| {
                        let health = client.health().map_err(|error| error.to_string())?;
                        let watermark = (health.generation, health.event_sequence);
                        if last_watermark == Some(watermark) {
                            if let Ok(mut state) = projection.write() {
                                if let Some(value) = state.accounts.get_mut(&account_id) {
                                    value.health = health;
                                    value.refreshed_at = Instant::now();
                                }
                            }
                            return Ok::<_, String>(None);
                        }
                        let value = AccountProjection {
                            health,
                            capabilities: client
                                .capabilities()
                                .map_err(|error| error.to_string())?,
                            balances: client.balances(None).map_err(|error| error.to_string())?,
                            positions: client.positions(None).map_err(|error| error.to_string())?,
                            refreshed_at: Instant::now(),
                        };
                        last_watermark = Some(watermark);
                        Ok(Some(value))
                    })();
                    if let Ok(Some(value)) = result {
                        if let Ok(mut state) = projection.write() {
                            state.accounts.insert(account_id.clone(), value);
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if let Some(path) = market_snapshot {
            let projection = Arc::clone(&projection);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let mut last_watermark = None;
                while !stop.load(Ordering::Acquire) {
                    if let Ok(watermark) =
                        kairos_market_contract::snapshot::read_latest_quotes_watermark(&path)
                    {
                        if last_watermark != Some(watermark) {
                            if let Ok(snapshot) =
                                kairos_market_contract::snapshot::read_latest_quotes_with_watermark(
                                    &path,
                                )
                            {
                                last_watermark = Some(watermark);
                                if let Ok(mut state) = projection.write() {
                                    state.market = Some(MarketProjection {
                                        snapshot,
                                        refreshed_at: Instant::now(),
                                    });
                                }
                            }
                        } else if let Ok(mut state) = projection.write() {
                            if let Some(value) = state.market.as_mut() {
                                value.refreshed_at = Instant::now();
                            }
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if let Some(path) = reference {
            let projection = Arc::clone(&projection);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let client = ReferenceQueryClient::connect(path);
                let mut last_watermark = None;
                while !stop.load(Ordering::Acquire) {
                    let result = (|| {
                        let health = client.health().map_err(|error| error.to_string())?;
                        let watermark = (health.generation, health.event_sequence);
                        if last_watermark == Some(watermark) {
                            if let Ok(mut state) = projection.write() {
                                if let Some(value) = state.reference.as_mut() {
                                    value.health = health;
                                    value.refreshed_at = Instant::now();
                                }
                            }
                            return Ok::<_, String>(None);
                        }
                        let value = ReferenceProjection {
                            health,
                            markets: client.active_markets().map_err(|error| error.to_string())?,
                            refreshed_at: Instant::now(),
                        };
                        last_watermark = Some(watermark);
                        Ok(Some(value))
                    })();
                    if let Ok(Some(value)) = result {
                        if let Ok(mut state) = projection.write() {
                            state.reference = Some(value);
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if let Some(path) = risk {
            let projection = Arc::clone(&projection);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let client = loop {
                    match kairos_risk_contract::client::RiskContractClient::connect(&path) {
                        Ok(client) => break client,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(PROJECTION_REFRESH)
                        }
                        Err(_) => return,
                    }
                };
                while !stop.load(Ordering::Acquire) {
                    if let Ok(health) = client.health() {
                        if let Ok(mut state) = projection.write() {
                            state.risk = Some(RiskProjection {
                                health,
                                refreshed_at: Instant::now(),
                            });
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        workers
    }

    fn account_projection(&self, account_id: &str) -> Result<AccountProjection, String> {
        let value = self
            .projection
            .read()
            .map_err(|_| "account projection lock poisoned".to_string())?
            .accounts
            .get(account_id)
            .cloned()
            .ok_or_else(|| format!("account projection is not ready: {account_id}"))?;
        if value.refreshed_at.elapsed() > PROJECTION_MAX_AGE {
            return Err(format!("account projection is stale: {account_id}"));
        }
        Ok(value)
    }

    fn reference_projection(&self) -> Result<ReferenceProjection, String> {
        let value = self
            .projection
            .read()
            .map_err(|_| "reference projection lock poisoned".to_string())?
            .reference
            .clone()
            .ok_or_else(|| "reference projection is not ready".to_string())?;
        if value.refreshed_at.elapsed() > PROJECTION_MAX_AGE {
            return Err("reference projection is stale".into());
        }
        Ok(value)
    }

    fn market_projection(&self) -> Result<MarketProjection, String> {
        let value = self
            .projection
            .read()
            .map_err(|_| "market projection lock poisoned".to_string())?
            .market
            .clone()
            .ok_or_else(|| "market projection is not ready".to_string())?;
        if value.refreshed_at.elapsed() > PROJECTION_MAX_AGE {
            return Err("market projection is stale".into());
        }
        Ok(value)
    }

    fn risk_projection(&self) -> Result<RiskProjection, String> {
        let value = self
            .projection
            .read()
            .map_err(|_| "risk projection lock poisoned".to_string())?
            .risk
            .clone()
            .ok_or_else(|| "risk projection is not ready".to_string())?;
        if value.refreshed_at.elapsed() > PROJECTION_MAX_AGE {
            return Err("risk projection is stale".into());
        }
        Ok(value)
    }

    fn refresh_watermarks(&mut self) {
        if let Ok(state) = self.projection.read() {
            self.dependency_watermarks.account = state
                .accounts
                .iter()
                .filter(|(_, value)| value.refreshed_at.elapsed() <= PROJECTION_MAX_AGE)
                .map(|(account_id, value)| {
                    (
                        account_id.clone(),
                        SnapshotWatermark {
                            generation: value.health.generation,
                            event_sequence: value.health.event_sequence,
                        },
                    )
                })
                .collect();
            self.dependency_watermarks.market =
                state.market.as_ref().map(|value| SnapshotWatermark {
                    generation: value.snapshot.generation,
                    event_sequence: value.snapshot.event_sequence,
                });
            self.dependency_watermarks.reference =
                state.reference.as_ref().map(|value| SnapshotWatermark {
                    generation: value.health.generation,
                    event_sequence: value.health.event_sequence,
                });
            self.dependency_watermarks.risk = state.risk.as_ref().map(|value| SnapshotWatermark {
                generation: value.health.generation,
                event_sequence: value.health.event_sequence,
            });
        }
    }

    fn reference_market(
        &self,
        market_id: Option<&str>,
        instrument_id: &str,
    ) -> Result<ReferenceMarket, String> {
        self.reference_projection()?
            .markets
            .into_iter()
            .find(|market| {
                market_id.is_some_and(|id| market.market_id == id)
                    || market.instrument_id == instrument_id
            })
            .ok_or_else(|| format!("reference market is not projected: {instrument_id}"))
    }

    fn health(&self, account_id: &str) -> Result<(), String> {
        let projection = self.account_projection(account_id)?;
        let response = projection.health;
        if response.status != "ready" || response.lease_valid == Some(false) {
            return Err(format!("account {account_id} is not ready"));
        }
        let can_trade = projection
            .capabilities
            .into_iter()
            .any(|item| item.account_id == account_id && item.can_trade);
        if !can_trade {
            return Err(format!(
                "account {account_id} does not have trade authorization"
            ));
        }
        Ok(())
    }
}

impl Drop for SocketExecutionPreflight {
    fn drop(&mut self) {
        self.projection_stop.store(true, Ordering::Release);
        for worker in self.projection_workers.drain(..) {
            let _ = worker.join();
        }
    }
}

enum PreflightRequest {
    Plan {
        intent: ExecuteStrategyIntent,
        reply: std::sync::mpsc::SyncSender<Result<Vec<SubmitOrder>, String>>,
    },
    Validate {
        request: SubmitOrder,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Prepare {
        request: SubmitOrder,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    PublishOrder {
        order: ExecutionOrder,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    PublishFill {
        fill: ExecutionFill,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Reserve {
        request: SubmitOrder,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Resize {
        order_id: String,
        remaining_quantity_mantissa: i64,
        quantity_scale: u8,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Release {
        order_id: String,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Consume {
        order_id: String,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
}

/// Composition-owned preflight gateway. All cross-process Account/Risk
/// commands execute on its worker thread; the Execution state owner only
/// waits for a typed result and never owns those clients.
pub struct QueuedExecutionPreflight {
    sender: std::sync::mpsc::SyncSender<PreflightRequest>,
    watermarks: Arc<RwLock<DependencyWatermarks>>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    circuit: DependencyCircuit,
}

#[derive(Default)]
struct DependencyCircuit {
    consecutive_failures: u32,
    open_until: Option<Instant>,
}

impl DependencyCircuit {
    fn permits(&self, bypass: bool) -> bool {
        bypass
            || self
                .open_until
                .map_or(true, |until| Instant::now() >= until)
    }

    fn record(&mut self, result: &Result<(), String>) {
        let Err(error) = result else {
            self.consecutive_failures = 0;
            self.open_until = None;
            return;
        };
        let dependency_error = [
            "transport",
            "http",
            "worker",
            "socket",
            "timeout",
            "stale",
            "not ready",
        ]
        .iter()
        .any(|marker| error.to_ascii_lowercase().contains(marker));
        if !dependency_error {
            return;
        }
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        if self.consecutive_failures >= 3 {
            self.open_until = Some(Instant::now() + Duration::from_secs(2));
        }
    }
}

impl QueuedExecutionPreflight {
    pub fn start(preflight: Box<dyn ExecutionPreflight>, capacity: usize) -> Result<Self, String> {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        let watermarks = Arc::new(RwLock::new(preflight.dependency_watermarks()));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker_watermarks = Arc::clone(&watermarks);
        let worker = std::thread::Builder::new()
            .name("execution-preflight".into())
            .spawn(move || Self::run_worker(preflight, receiver, worker_stop, worker_watermarks))
            .map_err(|error| format!("start execution preflight worker: {error}"))?;
        Ok(Self {
            sender,
            watermarks,
            stop,
            worker: Some(worker),
            circuit: DependencyCircuit::default(),
        })
    }

    fn run_worker(
        mut preflight: Box<dyn ExecutionPreflight>,
        receiver: std::sync::mpsc::Receiver<PreflightRequest>,
        stop: Arc<AtomicBool>,
        watermarks: Arc<RwLock<DependencyWatermarks>>,
    ) {
        while !stop.load(Ordering::Acquire) {
            let request = match receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(request) => request,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            };
            match request {
                PreflightRequest::Plan { intent, reply } => {
                    let _ = reply.send(preflight.plan_intent(&intent));
                }
                PreflightRequest::Validate { request, reply } => {
                    let _ = reply.send(preflight.validate_order(&request));
                }
                PreflightRequest::Prepare { request, reply } => {
                    let _ = reply.send(preflight.prepare_order(&request));
                }
                PreflightRequest::PublishOrder { order, reply } => {
                    let _ = reply.send(preflight.publish_order(&order));
                }
                PreflightRequest::PublishFill { fill, reply } => {
                    let _ = reply.send(preflight.publish_fill(&fill));
                }
                PreflightRequest::Reserve { request, reply } => {
                    let _ = reply.send(preflight.reserve_order(&request));
                }
                PreflightRequest::Resize {
                    order_id,
                    remaining_quantity_mantissa,
                    quantity_scale,
                    reply,
                } => {
                    let _ = reply.send(preflight.resize_order(
                        &order_id,
                        remaining_quantity_mantissa,
                        quantity_scale,
                    ));
                }
                PreflightRequest::Release { order_id, reply } => {
                    let _ = reply.send(preflight.release_order(&order_id));
                }
                PreflightRequest::Consume { order_id, reply } => {
                    let _ = reply.send(preflight.consume_order(&order_id));
                }
            }
            if let Ok(mut value) = watermarks.write() {
                *value = preflight.dependency_watermarks();
            }
        }
    }

    fn request<T>(
        &mut self,
        request: PreflightRequest,
        reply: std::sync::mpsc::Receiver<Result<T, String>>,
        bypass_circuit: bool,
    ) -> Result<T, String> {
        if !self.circuit.permits(bypass_circuit) {
            return Err("execution dependency circuit is open".into());
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut request = Some(request);
        loop {
            match self
                .sender
                .try_send(request.take().expect("preflight request present"))
            {
                Ok(()) => break,
                Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                    return Err("execution preflight worker is stopped".into())
                }
                Err(std::sync::mpsc::TrySendError::Full(value)) => {
                    if Instant::now() >= deadline {
                        return Err("execution preflight queue is full".into());
                    }
                    request = Some(value);
                    std::thread::sleep(Duration::from_millis(2));
                }
            }
        }
        let result = reply
            .recv()
            .map_err(|_| "execution preflight worker did not respond".to_string())?;
        self.circuit
            .record(&result.as_ref().map(|_| ()).map_err(|e| e.clone()));
        result
    }
}

impl Drop for QueuedExecutionPreflight {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

impl ExecutionPreflight for QueuedExecutionPreflight {
    fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.watermarks
            .read()
            .map(|value| value.clone())
            .unwrap_or_default()
    }

    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Plan {
                intent: intent.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn validate_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Validate {
                request: request.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn prepare_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Prepare {
                request: request.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::PublishOrder {
                order: order.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn publish_fill(&mut self, fill: &ExecutionFill) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::PublishFill {
                fill: fill.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn reserve_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Reserve {
                request: request.clone(),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn resize_order(
        &mut self,
        order_id: &str,
        remaining_quantity_mantissa: i64,
        quantity_scale: u8,
    ) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Resize {
                order_id: order_id.into(),
                remaining_quantity_mantissa,
                quantity_scale,
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn release_order(&mut self, order_id: &str) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Release {
                order_id: order_id.into(),
                reply: reply_tx,
            },
            reply_rx,
            true,
        )
    }

    fn consume_order(&mut self, order_id: &str) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Consume {
                order_id: order_id.into(),
                reply: reply_tx,
            },
            reply_rx,
            true,
        )
    }
}

impl ExecutionPreflight for SocketExecutionPreflight {
    fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.dependency_watermarks.clone()
    }

    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        self.refresh_watermarks();
        if self.market_snapshot.is_some() {
            let quotes = self.market_projection()?.snapshot.quotes;
            if quotes.is_empty() {
                return Err("market snapshot has no quotes".into());
            }
            if let Some(limit) = intent.limit_price_mantissa {
                validate_market_price(
                    &quotes,
                    intent,
                    limit,
                    intent.limit_price_scale.unwrap_or(intent.quantity_scale),
                )?;
            }
        }
        let mut orders = Vec::with_capacity(intent.account_ids.len());
        for (index, account_id) in intent.account_ids.iter().enumerate() {
            self.health(account_id)?;
            let positions = self.account_projection(account_id)?.positions;
            let current = find_position(&positions, &intent.instrument_id)
                .unwrap_or((0, intent.quantity_scale));
            let target = scale_decimal(
                intent.target_quantity_mantissa,
                intent.quantity_scale,
                current.1,
            )?;
            let delta = target
                .checked_sub(current.0)
                .ok_or_else(|| "intent quantity overflow".to_string())?;
            if delta == 0 {
                continue;
            }
            orders.push(SubmitOrder {
                order_id: format!("{}:order:{}", intent.intent_id, index),
                intent_id: Some(intent.intent_id.clone()),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                side: if delta > 0 {
                    OrderSide::Buy
                } else {
                    OrderSide::Sell
                },
                order_type: if intent.limit_price_mantissa.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity_mantissa: delta.unsigned_abs() as i64,
                quantity_scale: current.1,
                limit_price_mantissa: intent.limit_price_mantissa,
                limit_price_scale: intent.limit_price_scale,
                options: Default::default(),
            });
        }
        Ok(orders)
    }

    fn validate_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        self.health(&request.account_id)?;
        if request.quantity_mantissa <= 0 {
            return Err("order quantity must be positive".into());
        }
        if request.order_type == OrderType::Limit
            && request.limit_price_mantissa.unwrap_or_default() <= 0
        {
            return Err("limit price must be positive".into());
        }
        if self.reference.is_some() {
            let market =
                self.reference_market(request.market_id.as_deref(), &request.instrument_id)?;
            validate_reference_rules(&market, request)?;
        }
        let balances = self.account_projection(&request.account_id)?.balances;
        let asset = match request.side {
            OrderSide::Buy => request.options.quote_asset.clone().or_else(|| {
                request
                    .instrument_id
                    .strip_suffix("USDT")
                    .map(|_| "USDT".into())
            }),
            OrderSide::Sell => request
                .instrument_id
                .strip_suffix("USDT")
                .map(|value| value.to_string()),
        };
        if let Some(asset) = asset {
            let needed = if request.side == OrderSide::Buy {
                request
                    .quantity_mantissa
                    .checked_mul(request.limit_price_mantissa.unwrap_or(0))
                    .ok_or_else(|| "order notional overflow".to_string())?
            } else {
                request.quantity_mantissa
            };
            let available = find_available(&balances, &asset)
                .ok_or_else(|| format!("no available balance for {asset}"))?;
            if available < needed {
                return Err(format!("insufficient available balance for {asset}"));
            }
        }
        Ok(())
    }
    fn reserve_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let risk_projection = self.risk_projection()?;
        if risk_projection.health.status != "ready" {
            return Err("risk projection is not ready".into());
        }
        let reservation_id = format!("execution:{}", request.order_id);
        let amount = request
            .quantity_mantissa
            .checked_mul(request.limit_price_mantissa.unwrap_or(1))
            .ok_or_else(|| "risk notional overflow".to_string())?;
        self.risk_client()?
            .reserve(&ReserveRequest {
                reservation_id: reservation_id.clone(),
                assessment: Assessment {
                    request_id: request.order_id.clone(),
                    usages: vec![Usage {
                        metric: "notional".into(),
                        amount: RiskAmount {
                            mantissa: amount,
                            scale: request.quantity_scale,
                        },
                        budgets: vec![],
                    }],
                    at_unix_nanos: now_unix_nanos(),
                },
            })
            .map_err(|error| error.to_string())?;
        self.reservations.insert(
            request.order_id.clone(),
            format!("execution:{}", request.order_id),
        );
        self.reservation_amounts
            .insert(request.order_id.clone(), amount);
        self.reservation_quantities
            .insert(request.order_id.clone(), request.quantity_mantissa);
        Ok(())
    }
    fn prepare_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        self.account_client(&request.account_id)?
            .plan_order(&kairos_account_contract::client::OrderPlan {
                order_id: request.order_id.clone(),
                intent_id: request.intent_id.clone(),
                account_id: request.account_id.clone(),
                segment_key: request.segment_key.clone(),
                instrument_id: request.instrument_id.clone(),
                market_id: request.market_id.clone(),
                side: if request.side == OrderSide::Buy {
                    "Buy"
                } else {
                    "Sell"
                }
                .into(),
                quantity: AccountDecimal {
                    mantissa: request.quantity_mantissa,
                    scale: request.quantity_scale,
                },
                order_type: if request.order_type == OrderType::Limit {
                    "Limit"
                } else {
                    "Market"
                }
                .into(),
                limit_price: request.limit_price_mantissa.map(|mantissa| AccountDecimal {
                    mantissa,
                    scale: request.limit_price_scale.unwrap_or(request.quantity_scale),
                }),
            })
            .map_err(|error| error.to_string())?;
        self.orders.insert(
            request.order_id.clone(),
            (request.account_id.clone(), request.segment_key.clone()),
        );
        Ok(())
    }
    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        let account_id = &order.account_id;
        self.account_client(account_id)?
            .publish_order_event(&kairos_account_contract::client::OrderEvent {
                order_id: order.order_id.clone(),
                status: account_order_status(order.status).into(),
                venue_order_id: order.venue_order_id.clone(),
                filled_quantity: AccountDecimal {
                    mantissa: order.filled_quantity_mantissa,
                    scale: order.filled_quantity_scale,
                },
                occurred_at_unix_nanos: order.updated_at_unix_nanos,
                reason: order.reason.clone(),
            })
            .map_err(|error| error.to_string())
    }
    fn publish_fill(&mut self, fill: &ExecutionFill) -> Result<(), String> {
        let (account_id, segment_key) = self
            .orders
            .get(&fill.order_id)
            .cloned()
            .ok_or_else(|| format!("order fact is not prepared: {}", fill.order_id))?;
        self.account_client(&account_id)?
            .publish_fill(&kairos_account_contract::client::Fill {
                fill_id: fill.fill_id.clone(),
                order_id: fill.order_id.clone(),
                segment_key,
                instrument_id: fill.instrument_id.clone(),
                quantity: AccountDecimal {
                    mantissa: fill.quantity_mantissa,
                    scale: fill.quantity_scale,
                },
                price: AccountDecimal {
                    mantissa: fill.price_mantissa,
                    scale: fill.price_scale,
                },
                side: if fill.side == OrderSide::Buy {
                    "Buy"
                } else {
                    "Sell"
                }
                .into(),
                occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
            })
            .map_err(|error| error.to_string())
    }
    fn resize_order(
        &mut self,
        order_id: &str,
        remaining_quantity_mantissa: i64,
        quantity_scale: u8,
    ) -> Result<(), String> {
        let old_id = self
            .reservations
            .get(order_id)
            .cloned()
            .ok_or_else(|| "order reservation is missing".to_string())?;
        self.risk_client()?
            .release(&old_id)
            .map_err(|error| error.to_string())?;
        let previous = self
            .reservation_amounts
            .get(order_id)
            .copied()
            .unwrap_or_default();
        let original_quantity = self
            .reservation_quantities
            .get(order_id)
            .copied()
            .unwrap_or(remaining_quantity_mantissa)
            .max(1);
        let amount = (remaining_quantity_mantissa as i128)
            .checked_mul(previous.max(1) as i128)
            .and_then(|value| value.checked_div(original_quantity as i128))
            .and_then(|value| i64::try_from(value).ok())
            .unwrap_or(original_quantity);
        let new_id = format!("execution:{order_id}:remaining:{remaining_quantity_mantissa}");
        self.risk_client()?
            .reserve(&ReserveRequest {
                reservation_id: new_id.clone(),
                assessment: Assessment {
                    request_id: new_id.clone(),
                    usages: vec![Usage {
                        metric: "notional".into(),
                        amount: RiskAmount {
                            mantissa: amount,
                            scale: quantity_scale,
                        },
                        budgets: vec![],
                    }],
                    at_unix_nanos: now_unix_nanos(),
                },
            })
            .map_err(|error| error.to_string())?;
        self.reservations.insert(order_id.to_owned(), new_id);
        self.reservation_amounts.insert(order_id.to_owned(), amount);
        self.reservation_quantities
            .insert(order_id.to_owned(), remaining_quantity_mantissa);
        Ok(())
    }
    fn release_order(&mut self, order_id: &str) -> Result<(), String> {
        if self.risk.is_some() {
            let reservation_id = self.reservations.get(order_id).cloned();
            if let Some(reservation_id) = reservation_id {
                self.risk_client()?
                    .release(&reservation_id)
                    .map_err(|error| error.to_string())?;
            }
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        Ok(())
    }
    fn consume_order(&mut self, order_id: &str) -> Result<(), String> {
        if self.risk.is_some() {
            let reservation_id = self.reservations.get(order_id).cloned();
            if let Some(reservation_id) = reservation_id {
                self.risk_client()?
                    .consume(&reservation_id)
                    .map_err(|error| error.to_string())?;
            }
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        Ok(())
    }
}

fn validate_reference_rules(
    market: &kairos_reference_contract::query::ReferenceMarket,
    request: &SubmitOrder,
) -> Result<(), String> {
    let status = market.status.as_str();
    if !matches!(
        status.to_ascii_lowercase().as_str(),
        "active" | "listed" | "trading"
    ) {
        return Err("instrument or market is not tradable".into());
    }
    let quantity = request.quantity_mantissa as f64 / 10_f64.powi(request.quantity_scale as i32);
    if let Some(minimum) = market.minimum_quantity.as_deref().and_then(parse_float) {
        if quantity < minimum {
            return Err("order quantity is below the market minimum".into());
        }
    }
    if let Some(tick) = market.quantity_tick.as_deref().and_then(parse_float) {
        if !is_multiple(quantity, tick) {
            return Err("order quantity violates lot size".into());
        }
    }
    if let Some(price_mantissa) = request.limit_price_mantissa {
        let price = price_mantissa as f64
            / 10_f64.powi(request.limit_price_scale.unwrap_or(request.quantity_scale) as i32);
        if let Some(tick) = market.price_tick.as_deref().and_then(parse_float) {
            if !is_multiple(price, tick) {
                return Err("order price violates tick size".into());
            }
        }
        if let Some(minimum) = market.minimum_notional.as_deref().and_then(parse_float) {
            if price * quantity < minimum {
                return Err("order notional is below the market minimum".into());
            }
        }
    }
    Ok(())
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
        ExecutionOrderStatus::Unknown => "Unknown",
        ExecutionOrderStatus::Failed => "Unknown",
    }
}

fn parse_float(value: &str) -> Option<f64> {
    value.parse().ok().filter(|value: &f64| *value > 0.0)
}

fn is_multiple(value: f64, step: f64) -> bool {
    let quotient = value / step;
    (quotient - quotient.round()).abs() < 1e-8
}

fn find_available(
    response: &kairos_account_contract::client::BalancesResponse,
    asset: &str,
) -> Option<i64> {
    response
        .accounts
        .iter()
        .flat_map(|group| group.2.iter())
        .find(|balance| balance.asset_code.eq_ignore_ascii_case(asset))
        .and_then(|balance| balance.available.as_ref().map(|value| value.mantissa))
}

fn find_position(
    response: &kairos_account_contract::client::PositionsResponse,
    instrument: &str,
) -> Option<(i64, u8)> {
    response
        .accounts
        .iter()
        .flat_map(|group| group.2.iter())
        .find(|position| position.instrument_id.eq_ignore_ascii_case(instrument))
        .map(|position| (position.quantity.mantissa, position.quantity.scale))
}

fn scale_decimal(mantissa: i64, from: u8, to: u8) -> Result<i64, String> {
    if from == to {
        return Ok(mantissa);
    }
    if from < to {
        mantissa
            .checked_mul(
                10_i64
                    .checked_pow((to - from) as u32)
                    .ok_or_else(|| "quantity scale overflow".to_string())?,
            )
            .ok_or_else(|| "quantity scale overflow".to_string())
    } else {
        Ok(mantissa / 10_i64.pow((from - to) as u32))
    }
}

fn validate_market_price(
    quotes: &[kairos_market_contract::Quote],
    intent: &ExecuteStrategyIntent,
    limit_mantissa: i64,
    limit_scale: u8,
) -> Result<(), String> {
    let quote = quotes
        .iter()
        .find(|quote| {
            quote
                .instrument_id
                .eq_ignore_ascii_case(&intent.instrument_id)
        })
        .ok_or_else(|| "market quote is unavailable".to_string())?;
    let reference = quote
        .ask_price
        .clone()
        .or_else(|| quote.bid_price.clone())
        .ok_or_else(|| "market quote has no executable side".to_string())?;
    let limit = limit_mantissa as f64 / 10_f64.powi(limit_scale as i32);
    let reference = reference
        .parse::<f64>()
        .map_err(|_| "market quote price is invalid".to_string())?;
    if reference <= 0.0
        || ((limit - reference).abs() / reference) * 10_000.0 > MAX_PRICE_DEVIATION_BPS as f64
    {
        return Err("limit price deviates too far from the current market quote".into());
    }
    Ok(())
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::DependencyCircuit;
    use std::time::{Duration, Instant};

    #[test]
    fn dependency_circuit_opens_after_repeated_transport_failures() {
        let mut circuit = DependencyCircuit::default();
        let failure = Err("transport timeout".to_string());
        assert!(circuit.permits(false));
        circuit.record(&failure);
        circuit.record(&failure);
        assert!(circuit.permits(false));
        circuit.record(&failure);
        assert!(!circuit.permits(false));
        assert!(circuit.permits(true));
        circuit.open_until = Some(Instant::now() - Duration::from_millis(1));
        assert!(circuit.permits(false));
    }

    #[test]
    fn validation_errors_do_not_open_dependency_circuit() {
        let mut circuit = DependencyCircuit::default();
        let failure = Err("order quantity must be positive".to_string());
        for _ in 0..5 {
            circuit.record(&failure);
        }
        assert!(circuit.permits(false));
    }
}
