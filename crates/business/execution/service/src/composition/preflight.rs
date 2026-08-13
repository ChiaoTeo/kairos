//! Runtime intent planning and safety checks.
//!
//! Execution owns the plan and lifecycle, while this composition adapter
//! talks to the already-running Account/Risk/Market processes through their
//! Unix sockets.  No business state is cached here.

use crate::application::{
    DependencyWatermarks, ExecuteStrategyIntent, ExecutionPreflight, QuoteObservation,
    SnapshotWatermark, SubmitOrder,
};
use crate::domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
use kairos_domain_types::{
    InstrumentId, MarketId, Money, OrderId, Price, Quantity, SignedQuantity, StrategyId, UnixNanos,
};
use rust_decimal::Decimal;
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
use kairos_reference_contract::{ReferenceHealth, ReferenceMarket};
use kairos_risk_contract::client::RiskContractClient;
use kairos_risk_contract::model::{Amount as RiskAmount, AuthorizeRequest, Metric, RiskContext};

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
    health: ReferenceHealth,
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
    reference_database: Option<PathBuf>,
    risk: Option<PathBuf>,
    reservations: BTreeMap<String, String>,
    reservation_amounts: BTreeMap<String, RiskAmount>,
    reservation_quantities: BTreeMap<String, Quantity>,
    reservation_requests: BTreeMap<String, SubmitOrder>,
    orders: BTreeMap<String, (String, String)>,
    dependency_watermarks: DependencyWatermarks,
    projection: Arc<RwLock<DependencyProjection>>,
    projection_stop: Arc<AtomicBool>,
    projection_workers: Vec<JoinHandle<()>>,
    account_clients: BTreeMap<String, AccountContractClient>,
    risk_client: Option<RiskContractClient>,
    simulated_settlement: bool,
    allow_backtest_trade_authorization: bool,
    allow_backtest_reference_without_projection: bool,
    allow_backtest_balance_without_projection: bool,
    skip_backtest_risk_authorization: bool,
    business_time_unix_nanos: Option<u64>,
    reservation_ttl_nanos: u64,
}

const MAX_PRICE_DEVIATION_BPS: i64 = 500;

impl SocketExecutionPreflight {
    /// Backtest market events are delivered directly to the deterministic
    /// simulator.  They may be Bars without a live Quote snapshot, so the
    /// live quote projection must not reject an otherwise valid intent.
    pub fn without_market_snapshot(mut self) -> Self {
        self.market_snapshot = None;
        self
    }

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
        let market_snapshot = instance_root.clone().map(|root| {
            root.join("snapshots")
                .join("market")
                .join("market.snapshot")
        });
        let reference_database =
            instance_root.map(|root| root.join("reference").join("reference.sqlite"));
        let projection = Arc::new(RwLock::new(DependencyProjection::default()));
        let projection_stop = Arc::new(AtomicBool::new(false));
        let projection_workers = Self::start_projection_workers(
            accounts.clone(),
            market_snapshot.clone(),
            reference_database.clone(),
            endpoint("risk"),
            Arc::clone(&projection),
            Arc::clone(&projection_stop),
        );
        Ok(Self {
            accounts,
            market_snapshot,
            reference_database,
            risk: endpoint("risk"),
            reservations: BTreeMap::new(),
            reservation_amounts: BTreeMap::new(),
            reservation_quantities: BTreeMap::new(),
            reservation_requests: BTreeMap::new(),
            orders: BTreeMap::new(),
            dependency_watermarks: DependencyWatermarks::default(),
            projection,
            projection_stop,
            projection_workers,
            account_clients: BTreeMap::new(),
            risk_client: None,
            simulated_settlement: false,
            allow_backtest_trade_authorization: false,
            allow_backtest_reference_without_projection: false,
            allow_backtest_balance_without_projection: false,
            skip_backtest_risk_authorization: false,
            business_time_unix_nanos: None,
            reservation_ttl_nanos: 60_000_000_000,
        })
    }

    pub fn with_simulated_settlement(mut self, enabled: bool) -> Self {
        self.simulated_settlement = enabled;
        self
    }

    pub fn with_backtest_trade_authorization(mut self, enabled: bool) -> Self {
        self.allow_backtest_trade_authorization = enabled;
        if enabled {
            // A replay may advance business time by hours between the order
            // and the next executable Bar. Keep the reservation bounded, but
            // long enough for the configured replay window rather than using
            // the live 60-second acknowledgement deadline.
            self.reservation_ttl_nanos = 7 * 24 * 60 * 60 * 1_000_000_000;
        }
        self
    }

    pub fn with_backtest_reference_without_projection(mut self, enabled: bool) -> Self {
        self.allow_backtest_reference_without_projection = enabled;
        self
    }

    pub fn with_backtest_balance_without_projection(mut self, enabled: bool) -> Self {
        self.allow_backtest_balance_without_projection = enabled;
        self
    }

    pub fn with_backtest_risk_without_projection(mut self, enabled: bool) -> Self {
        self.skip_backtest_risk_authorization = enabled;
        self
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
        reference_database: Option<PathBuf>,
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
                let mut last_generation = None;
                while !stop.load(Ordering::Acquire) {
                    if let Ok(snapshot) =
                        kairos_market_contract::snapshot::read_latest_market_snapshot(&path)
                    {
                        if last_generation != Some(snapshot.generation) {
                            last_generation = Some(snapshot.generation);
                            if let Ok(mut state) = projection.write() {
                                state.market = Some(MarketProjection {
                                    snapshot,
                                    refreshed_at: Instant::now(),
                                });
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
        if let Some(database) = reference_database {
            let projection = Arc::clone(&projection);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let reader = loop {
                    match kairos_reference_contract::ReferenceSqliteReader::open(&database) {
                        Ok(reader) => break reader,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(PROJECTION_REFRESH)
                        }
                        Err(_) => return,
                    }
                };
                let mut last_watermark = None;
                while !stop.load(Ordering::Acquire) {
                    let result = (|| {
                        let watermark = reader.watermark().map_err(|error| error.to_string())?;
                        let health = ReferenceHealth {
                            status: "ready".into(),
                            generation: watermark.generation,
                            event_sequence: watermark.event_sequence,
                        };
                        let watermark = (watermark.generation, watermark.event_sequence);
                        if (health.generation, health.event_sequence) != watermark {
                            return Err("Reference health and snapshot watermark disagree".into());
                        }
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
                            generation: value.health.generation.into(),
                            event_sequence: value.health.event_sequence.into(),
                        },
                    )
                })
                .collect();
            self.dependency_watermarks.market =
                state.market.as_ref().map(|value| SnapshotWatermark {
                    generation: value.snapshot.generation.into(),
                    event_sequence: 0.into(),
                });
            self.dependency_watermarks.reference =
                state.reference.as_ref().map(|value| SnapshotWatermark {
                    generation: value.health.generation.into(),
                    event_sequence: value.health.event_sequence.into(),
                });
            self.dependency_watermarks.risk = state.risk.as_ref().map(|value| SnapshotWatermark {
                generation: value.health.generation.into(),
                event_sequence: value.health.event_sequence.into(),
            });
        }
    }

    /// Backtest commands are serialized by the StrategyHost. Refresh the
    /// account projection synchronously at that barrier so a fill settled by
    /// Account is visible to the very next target-position intent.
    fn refresh_account_projections(&mut self) -> Result<(), String> {
        let account_ids: Vec<String> = self.accounts.keys().cloned().collect();
        for account_id in account_ids {
            if !self.account_clients.contains_key(&account_id) {
                let socket = self
                    .accounts
                    .get(&account_id)
                    .ok_or_else(|| format!("account is not bound: {account_id}"))?
                    .clone();
                let client =
                    AccountContractClient::connect(socket).map_err(|error| error.to_string())?;
                self.account_clients.insert(account_id.clone(), client);
            }
            let client = self
                .account_clients
                .get(&account_id)
                .ok_or_else(|| format!("account client is unavailable: {account_id}"))?;
            let value = AccountProjection {
                health: client.health().map_err(|error| error.to_string())?,
                capabilities: client.capabilities().map_err(|error| error.to_string())?,
                balances: client.balances(None).map_err(|error| error.to_string())?,
                positions: client.positions(None).map_err(|error| error.to_string())?,
                refreshed_at: Instant::now(),
            };
            self.projection
                .write()
                .map_err(|_| "account projection lock poisoned".to_string())?
                .accounts
                .insert(account_id, value);
        }
        Ok(())
    }

    fn reference_market(
        &self,
        market_id: Option<&str>,
        instrument_id: &str,
    ) -> Result<ReferenceMarket, String> {
        let projected = self.reference_projection()?;
        let database = self
            .reference_database
            .as_ref()
            .ok_or_else(|| "Reference SQLite database is not configured".to_string())?;
        let reader = kairos_reference_contract::ReferenceSqliteReader::open(database)
            .map_err(|error| error.to_string())?;
        let watermark = reader.watermark().map_err(|error| error.to_string())?;
        if watermark.generation != projected.health.generation
            || watermark.event_sequence != projected.health.event_sequence
        {
            return Err("Reference projection watermark changed during preflight".into());
        }
        let markets = if let Some(market_id) = market_id {
            reader
                .market(market_id)
                .map_err(|error| error.to_string())?
                .into_iter()
                .collect()
        } else {
            reader
                .markets(&kairos_reference_contract::SqliteMarketQuery {
                    instrument_id: Some(instrument_id.to_owned()),
                    statuses: vec!["active".into(), "trading".into()],
                    limit: 2,
                    ..Default::default()
                })
                .map_err(|error| error.to_string())?
        };
        let [market] = markets.as_slice() else {
            return Err(format!(
                "Reference market resolution expected one match for {instrument_id}, found {}",
                markets.len()
            ));
        };
        Ok(market.clone())
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
        if !can_trade && !self.allow_backtest_trade_authorization {
            return Err(format!(
                "account {account_id} does not have trade authorization"
            ));
        }
        Ok(())
    }

    fn authorize_risk(
        &mut self,
        request: &SubmitOrder,
        reservation_id: String,
        amount: RiskAmount,
    ) -> Result<(), String> {
        let health = self.risk_projection()?.health;
        let account = self.account_projection(request.account_id.as_str())?;
        let (market_generation, market_event_sequence, market_is_fresh) =
            if self.market_snapshot.is_some() {
                let market = self.market_projection()?;
                let has_quote = market.snapshot.quotes.iter().any(|quote| {
                    quote.instrument_id == request.instrument_id
                        && request
                            .market_id
                            .as_ref()
                            .is_none_or(|market_id| quote.market_id == market_id.as_str())
                });
                (
                    market.snapshot.generation,
                    0,
                    has_quote
                        && market.snapshot.freshness.iter().all(|(_, value)| {
                            value.market_id
                                != request
                                    .market_id
                                    .as_ref()
                                    .map(MarketId::as_str)
                                    .unwrap_or_default()
                                || matches!(
                                    value.status,
                                    kairos_market_contract::model::DataFreshnessStatus::Current
                                )
                        }),
                )
            } else {
                (0, 0, true)
            };
        let available_margin = request
            .options
            .quote_asset
            .as_deref()
            .map(|asset| find_available(&account.balances, asset))
            .transpose()?
            .flatten()
            .unwrap_or(Decimal::ZERO)
            .max(Decimal::ZERO);
        let available_margin = risk_amount(available_margin)?;
        let business_time_unix_nanos = self.business_time_unix_nanos;
        let reservation_ttl_nanos = self.reservation_ttl_nanos;
        let decision = self
            .risk_client()?
            .authorize_and_reserve(&AuthorizeRequest {
                request_id: request.order_id.to_string(),
                idempotency_key: reservation_id.clone(),
                reservation_id,
                account_id: request.account_id.to_string(),
                strategy_id: request
                    .intent_id
                    .as_ref()
                    .map(ToString::to_string)
                    .unwrap_or_else(|| "execution".into()),
                instrument_id: request.instrument_id.to_string(),
                exchange_id: request
                    .market_id
                    .as_ref()
                    .map(ToString::to_string)
                    .unwrap_or_else(|| request.segment_key.to_string()),
                metric: Metric::Notional,
                amount,
                at_unix_nanos: request
                    .submitted_at_unix_nanos
                    .map(|value| value.get())
                    .or(business_time_unix_nanos)
                    .unwrap_or_else(now_unix_nanos),
                reservation_ttl_nanos,
                dependency_generation: health.generation,
                dependency_event_sequence: health.event_sequence,
                context: Some(RiskContext {
                    account_snapshot_watermark: account.health.generation,
                    market_freshness_watermark: market_generation.max(market_event_sequence),
                    portfolio_version: account.health.event_sequence,
                    current_exposure: RiskAmount {
                        mantissa: 0,
                        scale: 0,
                    },
                    current_margin: RiskAmount {
                        mantissa: 0,
                        scale: 0,
                    },
                    available_margin,
                    current_pnl: RiskAmount {
                        mantissa: 0,
                        scale: 0,
                    },
                    current_drawdown: RiskAmount {
                        mantissa: 0,
                        scale: 0,
                    },
                    market_is_fresh,
                    leverage_bps: 0,
                    price_deviation_bps: 0,
                    stress_loss: RiskAmount {
                        mantissa: 0,
                        scale: 0,
                    },
                }),
            })
            .map_err(|error| error.to_string())?;
        if !decision.allowed {
            return Err(if decision.violations.is_empty() {
                "risk authorization rejected order".into()
            } else {
                decision.violations.join("; ")
            });
        }
        if decision.reservation.is_none() {
            return Err("risk authorization did not create a reservation".into());
        }
        Ok(())
    }

    fn plan_explicit_legs(
        &mut self,
        intent: &ExecuteStrategyIntent,
    ) -> Result<Vec<SubmitOrder>, String> {
        let mut orders = Vec::with_capacity(intent.legs.len());
        for leg in &intent.legs {
            if leg.leg_id.as_str().trim().is_empty()
                || leg.account_id.as_str().trim().is_empty()
                || leg.segment_key.as_str().trim().is_empty()
                || leg.instrument_id.as_str().trim().is_empty()
            {
                return Err("explicit intent leg identity is required".into());
            }
            self.health(leg.account_id.as_str())?;
            let (side, quantity) = if leg.target_position {
                let positions = self.account_projection(leg.account_id.as_str())?.positions;
                let current =
                    find_position(&positions, leg.instrument_id.as_str())?.unwrap_or(Decimal::ZERO);
                let target = decimal_quantity(leg.quantity)?;
                let delta = target
                    .checked_sub(current)
                    .ok_or_else(|| "explicit intent leg quantity overflow".to_string())?;
                if delta == Decimal::ZERO {
                    continue;
                }
                (
                    if delta > Decimal::ZERO {
                        OrderSide::Buy
                    } else {
                        OrderSide::Sell
                    },
                    quantity_from_decimal(delta.abs())?,
                )
            } else {
                if leg.quantity.mantissa() <= 0 {
                    return Err("explicit intent leg quantity must be positive".into());
                }
                (leg.side, leg.quantity)
            };
            orders.push(SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, leg.leg_id))
                    .map_err(|error| error.to_string())?,
                intent_id: Some(intent.intent_id.clone()),
                strategy_id: Some(
                    StrategyId::new(intent.strategy_id.clone())
                        .map_err(|error| error.to_string())?,
                ),
                account_id: leg.account_id.clone(),
                segment_key: leg.segment_key.clone(),
                instrument_id: leg.instrument_id.clone(),
                market_id: leg.market_id.clone(),
                execution_access_id: leg
                    .execution_access_id
                    .clone()
                    .or_else(|| intent.execution_access_id.clone()),
                side,
                order_type: if leg.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity,
                limit_price: leg.limit_price,
                options: leg.options.clone(),
                submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
            });
        }
        if intent.intent_type == crate::domain::IntentType::PairArbitrage
            && (intent.min_edge_bps.is_some() || intent.max_slippage_bps.is_some())
        {
            let quotes = self.market_projection()?.snapshot.quotes;
            validate_pair_constraints(intent, &orders, &quotes)?;
        }
        if intent.intent_type == crate::domain::IntentType::QuoteProvisioning {
            validate_quote_provisioning(&orders)?;
        }
        if self.market_snapshot.is_some() {
            let quotes = self.market_projection()?.snapshot.quotes;
            validate_quote_freshness(&orders, &quotes)?;
        }
        Ok(orders)
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
    AdvanceTime {
        event_time_unix_nanos: u64,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Plan {
        intent: Box<ExecuteStrategyIntent>,
        reply: std::sync::mpsc::SyncSender<Result<Vec<SubmitOrder>, String>>,
    },
    LatestQuote {
        instrument_id: String,
        market_id: Option<String>,
        reply: std::sync::mpsc::SyncSender<Result<Option<QuoteObservation>, String>>,
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
        remaining_quantity: Quantity,
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
        bypass || self.open_until.is_none_or(|until| Instant::now() >= until)
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
                PreflightRequest::AdvanceTime {
                    event_time_unix_nanos,
                    reply,
                } => {
                    let _ = reply.send(preflight.advance_time(event_time_unix_nanos));
                }
                PreflightRequest::Plan { intent, reply } => {
                    let _ = reply.send(preflight.plan_intent(&intent));
                }
                PreflightRequest::LatestQuote {
                    instrument_id,
                    market_id,
                    reply,
                } => {
                    let _ =
                        reply.send(preflight.latest_quote(&instrument_id, market_id.as_deref()));
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
                    remaining_quantity,
                    reply,
                } => {
                    let _ = reply.send(preflight.resize_order(&order_id, remaining_quantity));
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
    fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::AdvanceTime {
                event_time_unix_nanos,
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

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
                intent: Box::new(intent.clone()),
                reply: reply_tx,
            },
            reply_rx,
            false,
        )
    }

    fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::LatestQuote {
                instrument_id: instrument_id.into(),
                market_id: market_id.map(str::to_owned),
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

    fn resize_order(&mut self, order_id: &str, remaining_quantity: Quantity) -> Result<(), String> {
        let (reply_tx, reply_rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PreflightRequest::Resize {
                order_id: order_id.into(),
                remaining_quantity,
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
    fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        if self
            .business_time_unix_nanos
            .is_some_and(|current| event_time_unix_nanos < current)
        {
            return Err("execution business time cannot move backwards".into());
        }
        self.business_time_unix_nanos = Some(event_time_unix_nanos);
        Ok(())
    }

    fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.dependency_watermarks.clone()
    }

    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        self.refresh_account_projections()?;
        self.refresh_watermarks();
        if !intent.legs.is_empty() {
            return self.plan_explicit_legs(intent);
        }
        if self.market_snapshot.is_some() {
            let quotes = self.market_projection()?.snapshot.quotes;
            if quotes.is_empty() {
                return Err("market snapshot has no quotes".into());
            }
            if let Some(limit) = intent.limit_price {
                validate_market_price(&quotes, intent, limit)?;
            }
        }
        let mut orders = Vec::with_capacity(intent.account_ids.len());
        for (index, account_id) in intent.account_ids.iter().enumerate() {
            self.health(account_id.as_str())?;
            // In replay, the preceding market barrier may have synchronously
            // settled a simulated fill through Account.  Read the authoritative
            // Account endpoint here instead of the asynchronously refreshed
            // projection: a worker refresh can otherwise publish an older
            // watermark between `refresh_account_projections` and this plan.
            let positions = self
                .account_client(account_id.as_str())?
                .positions(None)
                .map_err(|error| error.to_string())?;
            let current =
                find_position(&positions, intent.instrument_id.as_str())?.unwrap_or(Decimal::ZERO);
            let target = decimal_quantity(intent.target_quantity)?;
            let delta = target
                .checked_sub(current)
                .ok_or_else(|| "intent quantity overflow".to_string())?;
            if delta == Decimal::ZERO {
                continue;
            }
            let limit_price = intent.limit_price;
            orders.push(SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, index))
                    .map_err(|error| error.to_string())?,
                intent_id: Some(intent.intent_id.clone()),
                strategy_id: Some(
                    StrategyId::new(intent.strategy_id.clone())
                        .map_err(|error| error.to_string())?,
                ),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                execution_access_id: intent.execution_access_id.clone(),
                side: if delta > Decimal::ZERO {
                    OrderSide::Buy
                } else {
                    OrderSide::Sell
                },
                order_type: if intent.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity: quantity_from_decimal(delta.abs())?,
                limit_price,
                options: intent.order_options.clone(),
                submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
            });
        }
        Ok(orders)
    }

    fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        let snapshot = self.market_projection()?.snapshot;
        snapshot
            .quotes
            .into_iter()
            .find(|quote| {
                quote.instrument_id.eq_ignore_ascii_case(instrument_id)
                    && market_id.is_none_or(|value| quote.market_id == value)
            })
            .map(|quote| {
                Ok::<_, String>(QuoteObservation {
                    instrument_id: InstrumentId::new(quote.instrument_id)
                        .map_err(|error| error.to_string())?,
                    market_id: Some(
                        MarketId::new(quote.market_id).map_err(|error| error.to_string())?,
                    ),
                    bid_price: quote
                        .bid_price
                        .map(|value| value.parse::<Price>().map_err(|error| error.to_string()))
                        .transpose()?,
                    ask_price: quote
                        .ask_price
                        .map(|value| value.parse::<Price>().map_err(|error| error.to_string()))
                        .transpose()?,
                    observed_at_unix_nanos: UnixNanos::from(quote.observed_at_unix_nanos),
                })
            })
            .transpose()
    }

    fn validate_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        self.health(request.account_id.as_str())?;
        if request.quantity.mantissa() <= 0 {
            return Err("order quantity must be positive".into());
        }
        if request.order_type == OrderType::Limit
            && request
                .limit_price
                .is_some_and(|price| price.mantissa() <= 0)
        {
            return Err("limit price must be positive".into());
        }
        if !self.allow_backtest_reference_without_projection {
            let market = self.reference_market(
                request.market_id.as_ref().map(MarketId::as_str),
                request.instrument_id.as_str(),
            )?;
            validate_reference_rules(&market, request)?;
        }
        let balances = self
            .account_projection(request.account_id.as_str())?
            .balances;
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
        if !self.allow_backtest_balance_without_projection {
            if let Some(asset) = asset {
                let quantity = decimal_quantity(request.quantity)?;
                let needed = if request.side == OrderSide::Buy {
                    match request.limit_price {
                        Some(price) => quantity
                            .checked_mul(decimal_price(price)?)
                            .ok_or_else(|| "order notional overflow".to_string())?,
                        None => Decimal::ZERO,
                    }
                } else {
                    quantity
                };
                let available = find_available(&balances, &asset)?
                    .ok_or_else(|| format!("no available balance for {asset}"))?;
                if available < needed {
                    return Err(format!("insufficient available balance for {asset}"));
                }
            }
        }
        if let Some(policy) = request.options.maker.as_ref() {
            if let Some(max_inventory) = policy.max_inventory_abs {
                let positions = self
                    .account_projection(request.account_id.as_str())?
                    .positions;
                let current = find_position(&positions, request.instrument_id.as_str())?
                    .unwrap_or(Decimal::ZERO);
                let reserved = self
                    .reservation_requests
                    .values()
                    .filter(|value| {
                        value.account_id == request.account_id.as_str()
                            && value.instrument_id == request.instrument_id.as_str()
                    })
                    .try_fold(Decimal::ZERO, |total, value| -> Result<Decimal, String> {
                        let quantity = decimal_quantity(value.quantity)?;
                        let signed = if value.side == OrderSide::Buy {
                            quantity
                        } else {
                            -quantity
                        };
                        total
                            .checked_add(signed)
                            .ok_or_else(|| "maker inventory reservation overflow".to_string())
                    })?;
                let request_quantity = decimal_quantity(request.quantity)?;
                let signed_request = if request.side == OrderSide::Buy {
                    request_quantity
                } else {
                    -request_quantity
                };
                let projected = current
                    .checked_add(reserved)
                    .and_then(|value| value.checked_add(signed_request))
                    .ok_or_else(|| "maker inventory projection overflow".to_string())?;
                if projected.abs() > decimal_signed_quantity(max_inventory)?.abs() {
                    return Err(format!(
                        "maker inventory guard exceeded for {}: projected={}, limit={}",
                        request.instrument_id, projected, max_inventory
                    ));
                }
            }
        }
        if self.market_snapshot.is_some() {
            let quotes = self.market_projection()?.snapshot.quotes;
            validate_quote_freshness(std::slice::from_ref(request), &quotes)?;
        }
        Ok(())
    }
    fn reserve_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let risk_projection = self.risk_projection()?;
        if risk_projection.health.status != "ready" {
            return Err("risk projection is not ready".into());
        }
        let reservation_id = format!("execution:{}", request.order_id);
        let quantity = decimal_quantity(request.quantity)?;
        let price = request
            .limit_price
            .map(decimal_price)
            .transpose()?
            .unwrap_or(Decimal::ONE);
        let amount = risk_amount(
            quantity
                .checked_mul(price)
                .ok_or_else(|| "risk notional overflow".to_string())?,
        )?;
        if !self.skip_backtest_risk_authorization {
            self.authorize_risk(request, reservation_id.clone(), amount)?;
        }
        self.reservations.insert(
            request.order_id.to_string(),
            format!("execution:{}", request.order_id),
        );
        self.reservation_amounts
            .insert(request.order_id.to_string(), amount);
        self.reservation_quantities
            .insert(request.order_id.to_string(), request.quantity);
        self.reservation_requests
            .insert(request.order_id.to_string(), request.clone());
        Ok(())
    }

    fn prepare_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        // Account does not own execution planning. Execution owns the order
        // plan and lifecycle; Account receives only observed order/fill facts.
        let _ = request;
        self.orders.insert(
            request.order_id.to_string(),
            (
                request.account_id.to_string(),
                request.segment_key.to_string(),
            ),
        );
        self.reservation_requests
            .entry(request.order_id.to_string())
            .or_insert_with(|| request.clone());
        Ok(())
    }
    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        let account_id = &order.account_id;
        self.account_client(account_id)?
            .publish_order_event(&kairos_account_contract::client::OrderEvent {
                order_id: order.order_id.to_string(),
                status: account_order_status(order.status).into(),
                remote_order_id: order.remote_order_id.as_ref().map(ToString::to_string),
                filled_quantity: AccountDecimal {
                    mantissa: order.filled_quantity.mantissa(),
                    scale: order.filled_quantity.scale(),
                },
                occurred_at_unix_nanos: order.updated_at_unix_nanos.get(),
                reason: order.reason.clone(),
            })
            .map_err(|error| error.to_string())
    }
    fn publish_fill(&mut self, fill: &ExecutionFill) -> Result<(), String> {
        let (account_id, segment_key) = self
            .orders
            .get(fill.order_id.as_str())
            .cloned()
            .ok_or_else(|| format!("order fact is not prepared: {}", fill.order_id))?;
        let side = if fill.side == OrderSide::Buy {
            "buy"
        } else {
            "sell"
        };
        if self.simulated_settlement {
            let notional = decimal_quantity(fill.quantity)?
                .checked_mul(decimal_price(fill.price)?)
                .ok_or_else(|| "simulated settlement notional overflow".to_string())?;
            let settlement_delta = if fill.side == OrderSide::Buy {
                risk_amount(-notional)?
            } else {
                risk_amount(notional)?
            };
            return self
                .account_client(&account_id)?
                .publish_simulated_fill(&kairos_account_contract::client::SimulatedFill {
                    fill_id: fill.fill_id.to_string(),
                    order_id: fill.order_id.to_string(),
                    segment_key,
                    instrument_id: fill.instrument_id.to_string(),
                    quantity: AccountDecimal {
                        mantissa: fill.quantity.mantissa(),
                        scale: fill.quantity.scale(),
                    },
                    price: AccountDecimal {
                        mantissa: fill.price.mantissa(),
                        scale: fill.price.scale(),
                    },
                    side: side.into(),
                    settlement_asset: "USDT".into(),
                    settlement_delta: AccountDecimal {
                        mantissa: settlement_delta.mantissa,
                        scale: settlement_delta.scale,
                    },
                    fee_asset: fill.fee_currency.as_ref().map(ToString::to_string),
                    fee_amount: (fill.fee.mantissa() != 0).then_some(AccountDecimal {
                        mantissa: fill.fee.mantissa(),
                        scale: fill.fee.scale(),
                    }),
                    occurred_at_unix_nanos: fill.occurred_at_unix_nanos.get(),
                })
                .map_err(|error| error.to_string());
        }
        self.account_client(&account_id)?
            .publish_fill(&kairos_account_contract::client::Fill {
                fill_id: fill.fill_id.to_string(),
                order_id: fill.order_id.to_string(),
                segment_key,
                instrument_id: fill.instrument_id.to_string(),
                quantity: AccountDecimal {
                    mantissa: fill.quantity.mantissa(),
                    scale: fill.quantity.scale(),
                },
                price: AccountDecimal {
                    mantissa: fill.price.mantissa(),
                    scale: fill.price.scale(),
                },
                side: side.into(),
                occurred_at_unix_nanos: fill.occurred_at_unix_nanos.get(),
                fee_asset: fill.fee_currency.as_ref().map(ToString::to_string),
                fee_amount: Some(AccountDecimal {
                    mantissa: fill.fee.mantissa(),
                    scale: fill.fee.scale(),
                }),
            })
            .map_err(|error| error.to_string())
    }
    fn resize_order(&mut self, order_id: &str, remaining_quantity: Quantity) -> Result<(), String> {
        let old_id = self
            .reservations
            .get(order_id)
            .cloned()
            .ok_or_else(|| "order reservation is missing".to_string())?;
        let previous = self
            .reservation_amounts
            .get(order_id)
            .copied()
            .unwrap_or_default();
        let original_quantity = self
            .reservation_quantities
            .get(order_id)
            .copied()
            .unwrap_or(remaining_quantity);
        let previous = decimal_risk_amount(previous)?;
        let original_quantity = decimal_quantity(original_quantity)?;
        let remaining = decimal_quantity(remaining_quantity)?;
        let amount = risk_amount(
            previous
                .checked_mul(remaining)
                .and_then(|value| value.checked_div(original_quantity))
                .ok_or_else(|| "risk reservation resize overflow".to_string())?,
        )?;
        let mut replacement = self
            .reservation_requests
            .get(order_id)
            .cloned()
            .ok_or_else(|| "order reservation context is missing".to_string())?;
        replacement.quantity = remaining_quantity;
        let business_time_unix_nanos = self.business_time_unix_nanos;
        self.risk_client()?
            .resize(
                &old_id,
                &amount,
                business_time_unix_nanos.unwrap_or_else(now_unix_nanos),
            )
            .map_err(|error| error.to_string())?;
        self.reservations.insert(order_id.to_owned(), old_id);
        self.reservation_amounts.insert(order_id.to_owned(), amount);
        self.reservation_quantities
            .insert(order_id.to_owned(), remaining_quantity);
        self.reservation_requests
            .insert(order_id.to_owned(), replacement);
        Ok(())
    }
    fn release_order(&mut self, order_id: &str) -> Result<(), String> {
        if self.risk.is_some() && !self.skip_backtest_risk_authorization {
            let reservation_id = self.reservations.get(order_id).cloned();
            if let Some(reservation_id) = reservation_id {
                let business_time_unix_nanos = self.business_time_unix_nanos;
                self.risk_client()?
                    .release(
                        &reservation_id,
                        business_time_unix_nanos.unwrap_or_else(now_unix_nanos),
                    )
                    .map_err(|error| error.to_string())?;
            }
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        self.reservation_requests.remove(order_id);
        Ok(())
    }
    fn consume_order(&mut self, order_id: &str) -> Result<(), String> {
        if self.risk.is_some() && !self.skip_backtest_risk_authorization {
            let reservation_id = self.reservations.get(order_id).cloned();
            if let Some(reservation_id) = reservation_id {
                let business_time_unix_nanos = self.business_time_unix_nanos;
                self.risk_client()?
                    .consume(
                        &reservation_id,
                        business_time_unix_nanos.unwrap_or_else(now_unix_nanos),
                    )
                    .map_err(|error| error.to_string())?;
            }
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        self.reservation_requests.remove(order_id);
        Ok(())
    }
}

fn validate_reference_rules(market: &ReferenceMarket, request: &SubmitOrder) -> Result<(), String> {
    let status = market.status.as_str();
    if !matches!(
        status.to_ascii_lowercase().as_str(),
        "active" | "listed" | "trading"
    ) {
        return Err("instrument or market is not tradable".into());
    }
    if let Some(minimum) = market
        .minimum_quantity
        .as_deref()
        .map(str::parse::<Quantity>)
        .transpose()
        .map_err(|error| error.to_string())?
    {
        if request.quantity < minimum {
            return Err("order quantity is below the market minimum".into());
        }
    }
    if let Some(tick) = market
        .quantity_tick
        .as_deref()
        .map(str::parse::<Quantity>)
        .transpose()
        .map_err(|error| error.to_string())?
    {
        if !request
            .quantity
            .is_multiple_of(tick)
            .map_err(|error| error.to_string())?
        {
            return Err("order quantity violates lot size".into());
        }
    }
    if let Some(price_value) = request.limit_price {
        if let Some(tick) = market
            .price_tick
            .as_deref()
            .map(str::parse::<Price>)
            .transpose()
            .map_err(|error| error.to_string())?
        {
            if !price_value
                .is_multiple_of(tick)
                .map_err(|error| error.to_string())?
            {
                return Err("order price violates tick size".into());
            }
        }
        if let Some(minimum) = market
            .minimum_notional
            .as_deref()
            .map(str::parse::<Money>)
            .transpose()
            .map_err(|error| error.to_string())?
        {
            if price_value
                .checked_mul(request.quantity)
                .map_err(|error| error.to_string())?
                < minimum
            {
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

fn find_available(
    response: &kairos_account_contract::client::BalancesResponse,
    asset: &str,
) -> Result<Option<Decimal>, String> {
    response
        .accounts
        .iter()
        .flat_map(|group| group.2.iter())
        .find(|balance| balance.asset_code.eq_ignore_ascii_case(asset))
        .and_then(|balance| balance.available.as_ref())
        .map(|value| {
            Decimal::try_new(value.mantissa, u32::from(value.scale))
                .map_err(|_| "available balance cannot be represented as a decimal".to_string())
        })
        .transpose()
}

fn find_position(
    response: &kairos_account_contract::client::PositionsResponse,
    instrument: &str,
) -> Result<Option<Decimal>, String> {
    response
        .accounts
        .iter()
        .flat_map(|group| group.2.iter())
        .find(|position| position.instrument_id.eq_ignore_ascii_case(instrument))
        .map(|position| {
            Decimal::try_new(
                position.quantity.mantissa,
                u32::from(position.quantity.scale),
            )
            .map_err(|_| "position quantity cannot be represented as a decimal".to_string())
        })
        .transpose()
}

fn validate_market_price(
    quotes: &[kairos_market_contract::Quote],
    intent: &ExecuteStrategyIntent,
    limit: Price,
) -> Result<(), String> {
    let quote = quotes
        .iter()
        .find(|quote| {
            quote
                .instrument_id
                .eq_ignore_ascii_case(intent.instrument_id.as_str())
        })
        .ok_or_else(|| "market quote is unavailable".to_string())?;
    let reference = quote
        .ask_price
        .clone()
        .or_else(|| quote.bid_price.clone())
        .ok_or_else(|| "market quote has no executable side".to_string())?;
    let limit = decimal_price(limit)?;
    let reference = Decimal::from_str_exact(&reference)
        .map_err(|_| "market quote price is invalid".to_string())?;
    if reference <= Decimal::ZERO
        || ((limit - reference).abs() / reference) * Decimal::from(10_000_u32)
            > Decimal::from(MAX_PRICE_DEVIATION_BPS)
    {
        return Err("limit price deviates too far from the current market quote".into());
    }
    Ok(())
}

fn validate_pair_constraints(
    intent: &ExecuteStrategyIntent,
    orders: &[SubmitOrder],
    quotes: &[kairos_market_contract::Quote],
) -> Result<(), String> {
    if orders.len() < 2 {
        return Err("pair arbitrage requires at least two executable legs".into());
    }
    let mut buy_price = None;
    let mut sell_price = None;
    for order in orders {
        let quote = quotes
            .iter()
            .find(|quote| {
                quote
                    .instrument_id
                    .eq_ignore_ascii_case(&order.instrument_id)
                    && order
                        .market_id
                        .as_deref()
                        .is_none_or(|market_id| quote.market_id == market_id)
            })
            .ok_or_else(|| format!("market quote is unavailable: {}", order.instrument_id))?;
        let executable = match order.side {
            OrderSide::Buy => quote.ask_price.as_deref(),
            OrderSide::Sell => quote.bid_price.as_deref(),
        }
        .ok_or_else(|| format!("quote has no executable side: {}", order.instrument_id))?
        .parse::<Decimal>()
        .map_err(|_| format!("invalid market quote price: {}", order.instrument_id))?;
        if executable <= Decimal::ZERO {
            return Err(format!(
                "market quote is not positive: {}",
                order.instrument_id
            ));
        }
        match order.side {
            OrderSide::Buy => buy_price = Some(executable),
            OrderSide::Sell => sell_price = Some(executable),
        }
        if let (Some(limit), Some(max_slippage)) = (
            order
                .limit_price
                .map(|price| (price.mantissa(), price.scale())),
            intent.max_slippage_bps,
        ) {
            let limit = Decimal::try_new(limit.0, u32::from(limit.1))
                .map_err(|_| "pair limit price is invalid".to_string())?;
            let slippage_bps = match order.side {
                OrderSide::Buy => (limit - executable) / executable * Decimal::from(10_000_u32),
                OrderSide::Sell => (executable - limit) / executable * Decimal::from(10_000_u32),
            };
            if slippage_bps > Decimal::from(max_slippage) {
                return Err(format!(
                    "pair leg {} exceeds max slippage: {:.2} bps > {} bps",
                    order.instrument_id, slippage_bps, max_slippage
                ));
            }
        }
    }
    if let Some(min_edge) = intent.min_edge_bps {
        let buy = buy_price.ok_or_else(|| "pair arbitrage requires a buy leg".to_string())?;
        let sell = sell_price.ok_or_else(|| "pair arbitrage requires a sell leg".to_string())?;
        let gross_edge_bps = (sell - buy) / buy * Decimal::from(10_000_u32);
        let net_edge_bps =
            gross_edge_bps - Decimal::from(intent.estimated_fee_bps.unwrap_or_default());
        if net_edge_bps < Decimal::from(min_edge) {
            return Err(format!(
                "pair net edge is below minimum: {:.2} bps < {} bps (gross={:.2}, fees={})",
                net_edge_bps,
                min_edge,
                gross_edge_bps,
                intent.estimated_fee_bps.unwrap_or_default()
            ));
        }
    }
    Ok(())
}

fn validate_quote_provisioning(orders: &[SubmitOrder]) -> Result<(), String> {
    let bid = orders
        .iter()
        .find(|order| order.side == OrderSide::Buy)
        .ok_or_else(|| "quote provisioning requires a bid leg".to_string())?;
    let ask = orders
        .iter()
        .find(|order| order.side == OrderSide::Sell)
        .ok_or_else(|| "quote provisioning requires an ask leg".to_string())?;
    if bid.instrument_id != ask.instrument_id || bid.market_id != ask.market_id {
        return Err(
            "quote provisioning bid and ask must target the same instrument and market".into(),
        );
    }
    if bid.options.post_only != Some(true) || ask.options.post_only != Some(true) {
        return Err("quote provisioning requires post_only on both legs".into());
    }
    let bid_price = bid
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or_else(|| "quote provisioning bid must be a limit order".to_string())?;
    let ask_price = ask
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or_else(|| "quote provisioning ask must be a limit order".to_string())?;
    let bid_value = Decimal::try_new(bid_price.0, u32::from(bid_price.1))
        .map_err(|_| "quote bid price is invalid".to_string())?;
    let ask_value = Decimal::try_new(ask_price.0, u32::from(ask_price.1))
        .map_err(|_| "quote ask price is invalid".to_string())?;
    if bid_value <= Decimal::ZERO || ask_value <= bid_value {
        return Err("quote provisioning requires a positive bid below ask".into());
    }
    Ok(())
}

fn validate_quote_freshness(
    orders: &[SubmitOrder],
    quotes: &[kairos_market_contract::Quote],
) -> Result<(), String> {
    let now = now_unix_nanos();
    for order in orders {
        let Some(max_age) = order
            .options
            .maker
            .as_ref()
            .and_then(|policy| policy.max_quote_age)
        else {
            continue;
        };
        let quote = quotes
            .iter()
            .find(|quote| {
                quote
                    .instrument_id
                    .eq_ignore_ascii_case(&order.instrument_id)
                    && order
                        .market_id
                        .as_deref()
                        .is_none_or(|market_id| quote.market_id == market_id)
            })
            .ok_or_else(|| format!("market quote is unavailable: {}", order.instrument_id))?;
        let age = now.saturating_sub(quote.observed_at_unix_nanos);
        if age > max_age.get() {
            return Err(format!(
                "market quote is stale for {}: age={}ms exceeds {}ms",
                order.instrument_id,
                age / 1_000_000,
                max_age.get() / 1_000_000
            ));
        }
    }
    Ok(())
}

fn decimal_quantity(value: Quantity) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "quantity cannot be represented as a decimal".to_string())
}

fn decimal_signed_quantity(value: SignedQuantity) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "signed quantity cannot be represented as a decimal".to_string())
}

fn quantity_from_decimal(value: Decimal) -> Result<Quantity, String> {
    let value = value.normalize();
    if value < Decimal::ZERO || value.scale() > u32::from(kairos_domain_types::MAX_DECIMAL_SCALE) {
        return Err("quantity is outside the supported decimal range".into());
    }
    Quantity::new(
        i64::try_from(value.mantissa())
            .map_err(|_| "quantity exceeds Decimal64 range".to_string())?,
        value.scale() as u8,
    )
    .map_err(|error| error.to_string())
}

fn decimal_price(value: Price) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "price cannot be represented as a decimal".to_string())
}

fn decimal_risk_amount(value: RiskAmount) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa, u32::from(value.scale))
        .map_err(|_| "risk amount cannot be represented as a decimal".to_string())
}

fn risk_amount(value: Decimal) -> Result<RiskAmount, String> {
    let value = value.normalize();
    if value.scale() > u32::from(kairos_domain_types::MAX_DECIMAL_SCALE) {
        return Err("risk amount exceeds 18 fractional digits".into());
    }
    Ok(RiskAmount {
        mantissa: i64::try_from(value.mantissa())
            .map_err(|_| "risk amount exceeds Decimal64 range".to_string())?,
        scale: value.scale() as u8,
    })
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::{
        decimal_price, decimal_quantity, risk_amount, DependencyCircuit, DependencyProjection,
        SocketExecutionPreflight,
    };
    use kairos_domain_types::{Price, Quantity};
    use std::collections::BTreeMap;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, RwLock,
    };
    use std::time::{Duration, Instant};

    #[test]
    fn reference_database_builds_execution_watermark() {
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("reference.sqlite");
        rusqlite::Connection::open(&database).unwrap().execute_batch("CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, generation INTEGER NOT NULL, event_sequence INTEGER NOT NULL, committed_at_unix_nanos INTEGER NOT NULL); INSERT INTO reference_meta VALUES(1,1,7,11,0);").unwrap();

        let projection = Arc::new(RwLock::new(DependencyProjection::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let workers = SocketExecutionPreflight::start_projection_workers(
            BTreeMap::new(),
            None,
            Some(database),
            None,
            Arc::clone(&projection),
            Arc::clone(&stop),
        );
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if projection.read().unwrap().reference.is_some() {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Execution did not project the Reference snapshot"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        stop.store(true, Ordering::Release);
        for worker in workers {
            worker.join().unwrap();
        }

        let state = projection.read().unwrap();
        let reference = state.reference.as_ref().unwrap();
        assert_eq!(reference.health.generation, 7);
        assert_eq!(reference.health.event_sequence, 11);
    }

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

    #[test]
    fn risk_notional_preserves_quantity_and_price_scales() {
        let quantity = decimal_quantity(Quantity::new(2, 0).unwrap()).unwrap();
        let price = decimal_price(Price::new(1_005, 1).unwrap()).unwrap();
        let amount = risk_amount(quantity * price).unwrap();

        assert_eq!((amount.mantissa, amount.scale), (201, 0));
    }
}
