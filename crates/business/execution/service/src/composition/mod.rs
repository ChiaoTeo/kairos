use crate::application::{
    ExecutionAuditEvent, ExecutionAuditQuery, ExecutionAuditSink, ExecutionEvent,
    ExecutionSnapshot, IntentEvent,
};
use crate::services::persistence::{
    ExecutionOutboxEntry, ExecutionOutboxEvent, ExecutionStateStore,
};
use kairos_integration::application::{
    Connection, ExecutionStreamConnection, OrderEntryConnection, OrderQueryConnection,
};
use kairos_integration::domain::{
    AccessScope, ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState,
    DecimalValue, IntegrationCapability, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus,
    ProductFamily, TransportKind,
};
use kairos_integration::{ConnectionSpec, Integration};
use std::path::PathBuf;

pub struct SharedExecutionSnapshotPublisher {
    inner: kairos_execution_contract::encoding::SharedExecutionSnapshotPublisher,
}

impl SharedExecutionSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_execution_contract::encoding::SharedExecutionSnapshotPublisher::create(
                path, slot_size, actor_id,
            )?,
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_execution_contract::model::ExecutionSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

pub struct SharedIntentSnapshotPublisher {
    inner: kairos_execution_contract::encoding::SharedIntentSnapshotPublisher,
}

impl SharedIntentSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_execution_contract::encoding::SharedIntentSnapshotPublisher::create(
                path, slot_size, actor_id,
            )?,
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_execution_contract::model::ExecutionSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)
    }
}

pub struct SimulatedOrderEntry {
    state: ConnectionState,
}

#[derive(Clone, Debug)]
pub struct ExecutionConnectionOptions {
    pub provider: String,
    pub product: String,
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
    pub base_url: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

pub fn compose_order_entry(
    options: &ExecutionConnectionOptions,
) -> Result<Box<dyn OrderEntryConnection>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    if provider == "simulated" {
        return Ok(Box::new(SimulatedOrderEntry::default()));
    }
    let product_name = options.product.trim().to_ascii_lowercase();
    let (integration, product) = match provider.as_str() {
        "ibkr" => (
            Integration::new().with_ibkr_order_entry(
                options.host.clone(),
                options.port,
                options.client_id,
            ),
            ProductFamily::Spot,
        ),
        "binance" => match product_name.as_str() {
            "equity" | "stocks" => (
                Integration::new().with_binance_equity_order_entry(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Equity,
            ),
            "spot" => (
                Integration::new().with_binance_spot_order_entry(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Spot,
            ),
            "cross-margin" | "margin" => (
                Integration::new()
                    .with_binance_margin_order_entry(
                        ProductFamily::CrossMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CrossMargin,
            ),
            "isolated-margin" => (
                Integration::new()
                    .with_binance_margin_order_entry(
                        ProductFamily::IsolatedMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::IsolatedMargin,
            ),
            "options" => (
                Integration::new().with_binance_options_order_entry(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Options,
            ),
            "usd-m-futures" | "swap" => (
                Integration::new()
                    .with_binance_futures_order_entry(
                        ProductFamily::UsdMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::UsdMFutures,
            ),
            "coin-m-futures" | "futures" => (
                Integration::new()
                    .with_binance_futures_order_entry(
                        ProductFamily::CoinMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CoinMFutures,
            ),
            _ => {
                return Err(format!(
                    "unsupported Binance execution product: {product_name}"
                ))
            }
        },
        "okx" | "okex" => {
            let product = match product_name.as_str() {
                "spot" => ProductFamily::Spot,
                "cross-margin" | "margin" => ProductFamily::CrossMargin,
                "isolated-margin" => ProductFamily::IsolatedMargin,
                "swap" | "usd-m-futures" => ProductFamily::UsdMFutures,
                "futures" | "coin-m-futures" => ProductFamily::CoinMFutures,
                "options" => ProductFamily::Options,
                _ => return Err(format!("unsupported OKX execution product: {product_name}")),
            };
            (
                Integration::new()
                    .with_okx_order_entry(
                        product,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.passphrase.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                product,
            )
        }
        _ => return Err(format!("unsupported execution provider: {provider}")),
    };
    integration
        .connect_order_entry(&ConnectionSpec {
            connection_id: format!("execution.{provider}.{product_name}.rest"),
            route: if provider == "okex" {
                kairos_integration::IntegrationRoute::exchange("okx")
            } else {
                kairos_integration::IntegrationRoute::exchange(provider)
            },
            product: Some(product),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("execution".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())
}

pub fn compose_order_query(
    options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn OrderQueryConnection>>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    let product_family = match product.as_str() {
        "equity" | "stocks" => {
            return Ok(Some(
                Integration::new()
                    .with_binance_equity_order_query(
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .connect_order_query(&ConnectionSpec {
                        connection_id: "execution.binance.equity.order-read.rest".into(),
                        route: kairos_integration::IntegrationRoute::exchange("binance"),
                        product: Some(ProductFamily::Equity),
                        access: AccessScope::Private,
                        transport: TransportKind::Rest,
                        capability: IntegrationCapability::OrderRead,
                        credential_id: Some("execution".into()),
                        asset_type: None,
                    })
                    .map_err(|error| error.to_string())?,
            ))
        }
        "spot" => ProductFamily::Spot,
        "usd-m-futures" | "swap" => ProductFamily::UsdMFutures,
        "coin-m-futures" | "futures" => ProductFamily::CoinMFutures,
        "options" => ProductFamily::Options,
        _ => return Ok(None),
    };
    let integration = if provider == "binance" {
        Integration::new().with_binance_order_query(
            product_family,
            options.api_key.clone(),
            options.secret.clone(),
            options.base_url.clone(),
        )
    } else if provider == "okx" || provider == "okex" {
        Integration::new().with_okx_order_query(
            product_family,
            options.api_key.clone(),
            options.secret.clone(),
            options.passphrase.clone(),
            options.base_url.clone(),
        )
    } else {
        return Ok(None);
    };
    let connection = integration
        .connect_order_query(&ConnectionSpec {
            connection_id: "execution.binance.equity.order-read.rest".into(),
            route: if provider == "okex" {
                kairos_integration::IntegrationRoute::exchange("okx")
            } else {
                kairos_integration::IntegrationRoute::exchange(provider.clone())
            },
            product: Some(product_family),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderRead,
            credential_id: Some("execution".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    Ok(Some(connection))
}

pub fn compose_execution_stream(
    options: &ExecutionConnectionOptions,
) -> Result<Option<Box<dyn ExecutionStreamConnection>>, String> {
    let provider = options.provider.trim().to_ascii_lowercase();
    if provider != "ibkr" {
        return Ok(None);
    }
    let integration = Integration::new().with_ibkr_execution_stream(
        options.host.clone(),
        options.port,
        options.client_id,
        "",
        None,
    );
    let connection = integration
        .connect_execution_stream(&ConnectionSpec {
            connection_id: "execution.ibkr.equity.stream".into(),
            route: kairos_integration::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::ExecutionStream,
            credential_id: Some("execution".into()),
            asset_type: Some(kairos_integration::domain::AssetType::Equity),
        })
        .map_err(|error| error.to_string())?;
    Ok(Some(connection))
}

impl Default for SimulatedOrderEntry {
    fn default() -> Self {
        let identity = ConnectionIdentity::new(
            "execution.simulated.order-entry",
            kairos_integration::IntegrationRoute::exchange("simulated"),
            Some(ProductFamily::Spot),
            AccessScope::Private,
            TransportKind::Rest,
            IntegrationCapability::OrderEntry,
        )
        .expect("static simulated connection identity");
        Self {
            state: ConnectionState::new(identity),
        }
    }
}

impl Connection for SimulatedOrderEntry {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }
    fn state(&self) -> &ConnectionState {
        &self.state
    }
    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = true;
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl OrderEntryConnection for SimulatedOrderEntry {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Accepted,
            venue_order_id: Some(format!("simulated:{}", request.order_id)),
            filled_quantity: Some(DecimalValue::new(0, request.quantity.scale)),
            occurred_at_unix_nanos: now_nanos(),
            reason: String::new(),
        })
    }
    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        Ok(OrderEntryEvent {
            order_id: request.order_id.clone(),
            status: OrderEntryStatus::Canceled,
            venue_order_id: Some(venue_order_id.to_string()),
            filled_quantity: None,
            occurred_at_unix_nanos: at_unix_nanos,
            reason: String::new(),
        })
    }
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

pub struct MemoryStateStore(pub Option<ExecutionSnapshot>);
impl ExecutionStateStore for MemoryStateStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        Ok(self.0.clone())
    }
    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.0 = Some(snapshot.clone());
        Ok(())
    }
}

pub struct MemoryExecutionAudit(pub Vec<ExecutionEvent>);
impl MemoryExecutionAudit {
    pub fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.0.push(event.clone());
        Ok(())
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        Ok(self
            .0
            .iter()
            .enumerate()
            .map(|(index, event)| ExecutionAuditEvent {
                sequence: index as u64 + 1,
                order_id: event.order_id.clone(),
                status: format!("{:?}", event.status).to_ascii_lowercase(),
                venue_order_id: event.venue_order_id.clone(),
                occurred_at_unix_nanos: event.occurred_at_unix_nanos,
                reason: event.reason.clone(),
            })
            .filter(|event| {
                query
                    .order_id
                    .as_deref()
                    .is_none_or(|value| event.order_id == value)
                    && query
                        .status
                        .as_deref()
                        .is_none_or(|value| event.status.eq_ignore_ascii_case(value))
            })
            .take(query.limit.unwrap_or(u32::MAX) as usize)
            .collect())
    }
}

impl ExecutionAuditSink for MemoryExecutionAudit {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        Self::publish(self, event)
    }

    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String> {
        Self::query(self, query)
    }
}

pub struct FileExecutionStore {
    path: PathBuf,
}

/// Durable execution checkpoint store.
///
/// Each checkpoint is appended inside SQLite WAL instead of replacing a
/// pretty-printed JSON file.  Startup reads the newest checkpoint; compaction
/// can later delete older rows without changing the application boundary.
pub struct SqliteExecutionStore {
    connection: rusqlite::Connection,
}

pub struct SqliteExecutionAudit {
    path: PathBuf,
}

impl SqliteExecutionAudit {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl ExecutionAuditSink for SqliteExecutionAudit {
    fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        Self::publish(self, event)
    }

    fn publish_intent(&mut self, event: &IntentEvent) -> Result<(), String> {
        Self::publish_intent(self, event)
    }

    fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intent_events: &[IntentEvent],
    ) -> Result<(), String> {
        Self::publish_batch(self, events, intent_events)
    }

    fn query(&mut self, query: &ExecutionAuditQuery) -> Result<Vec<ExecutionAuditEvent>, String> {
        Self::query(self, query)
    }
}

impl SqliteExecutionAudit {
    pub fn publish_batch(
        &mut self,
        events: &[ExecutionEvent],
        intent_events: &[IntentEvent],
    ) -> Result<(), String> {
        if events.is_empty() && intent_events.is_empty() {
            return Ok(());
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let connection =
            rusqlite::Connection::open(&self.path).map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = NORMAL;
                 PRAGMA busy_timeout = 5000;
                 CREATE TABLE IF NOT EXISTS execution_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    venue_order_id TEXT,
                    occurred_at_unix_nanos INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    event_key TEXT NOT NULL UNIQUE
                 );
                 CREATE TABLE IF NOT EXISTS intent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    order_ids TEXT NOT NULL,
                    completed_quantity_mantissa INTEGER NOT NULL,
                    occurred_at_unix_nanos INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    event_key TEXT NOT NULL UNIQUE
                 );",
            )
            .map_err(|error| error.to_string())?;
        connection
            .execute_batch("BEGIN IMMEDIATE")
            .map_err(|error| error.to_string())?;
        let result = (|| {
            for event in events {
                connection
                    .execute(
                        "INSERT OR IGNORE INTO execution_events
                         (order_id, status, venue_order_id, occurred_at_unix_nanos, reason, event_key)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                        rusqlite::params![
                            event.order_id,
                            format!("{:?}", event.status).to_ascii_lowercase(),
                            event.venue_order_id,
                            event.occurred_at_unix_nanos as i64,
                            event.reason,
                            format!(
                                "{}|{:?}|{}|{}|{}",
                                event.order_id,
                                event.status,
                                event.venue_order_id.as_deref().unwrap_or_default(),
                                event.occurred_at_unix_nanos,
                                event.reason
                            ),
                        ],
                    )
                    .map_err(|error| error.to_string())?;
            }
            for event in intent_events {
                let order_ids =
                    serde_json::to_string(&event.order_ids).map_err(|error| error.to_string())?;
                connection
                    .execute(
                        "INSERT OR IGNORE INTO intent_events
                         (intent_id, status, order_ids, completed_quantity_mantissa,
                          occurred_at_unix_nanos, reason, event_key)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                        rusqlite::params![
                            event.intent_id,
                            format!("{:?}", event.status).to_ascii_lowercase(),
                            order_ids,
                            event.completed_quantity_mantissa,
                            event.occurred_at_unix_nanos as i64,
                            event.reason,
                            format!(
                                "{}|{:?}|{}|{}|{}",
                                event.intent_id,
                                event.status,
                                event.occurred_at_unix_nanos,
                                event.completed_quantity_mantissa,
                                event.reason
                            ),
                        ],
                    )
                    .map_err(|error| error.to_string())?;
            }
            Ok::<_, String>(())
        })();
        match result {
            Ok(()) => connection
                .execute_batch("COMMIT")
                .map_err(|error| error.to_string()),
            Err(error) => {
                let _ = connection.execute_batch("ROLLBACK");
                Err(error)
            }
        }
    }

    pub fn publish_intent(&mut self, event: &IntentEvent) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let connection =
            rusqlite::Connection::open(&self.path).map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS intent_events (\
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,\
                    intent_id TEXT NOT NULL,\
                    status TEXT NOT NULL,\
                    order_ids TEXT NOT NULL,\
                    completed_quantity_mantissa INTEGER NOT NULL,\
                    occurred_at_unix_nanos INTEGER NOT NULL,\
                    reason TEXT NOT NULL,\
                    event_key TEXT NOT NULL UNIQUE\
                )",
            )
            .map_err(|error| error.to_string())?;
        let order_ids =
            serde_json::to_string(&event.order_ids).map_err(|error| error.to_string())?;
        connection
            .execute(
                "INSERT OR IGNORE INTO intent_events (intent_id, status, order_ids, completed_quantity_mantissa, occurred_at_unix_nanos, reason, event_key) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                rusqlite::params![
                    event.intent_id,
                    format!("{:?}", event.status).to_ascii_lowercase(),
                    order_ids,
                    event.completed_quantity_mantissa,
                    event.occurred_at_unix_nanos as i64,
                    event.reason,
                    format!("{}|{:?}|{}|{}|{}", event.intent_id, event.status, event.occurred_at_unix_nanos, event.completed_quantity_mantissa, event.reason),
                ],
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    pub fn publish(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let connection =
            rusqlite::Connection::open(&self.path).map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "CREATE TABLE IF NOT EXISTS execution_events (\
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,\
                    order_id TEXT NOT NULL,\
                    status TEXT NOT NULL,\
                    venue_order_id TEXT,\
                    occurred_at_unix_nanos INTEGER NOT NULL,\
                    reason TEXT NOT NULL,\
                    event_key TEXT NOT NULL UNIQUE\
                )",
            )
            .map_err(|error| error.to_string())?;
        connection
            .execute(
                "INSERT OR IGNORE INTO execution_events (order_id, status, venue_order_id, occurred_at_unix_nanos, reason, event_key) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![
                    event.order_id,
                    format!("{:?}", event.status).to_ascii_lowercase(),
                    event.venue_order_id,
                    event.occurred_at_unix_nanos as i64,
                    event.reason,
                    format!(
                        "{}|{:?}|{}|{}|{}",
                        event.order_id,
                        event.status,
                        event.venue_order_id.as_deref().unwrap_or_default(),
                        event.occurred_at_unix_nanos,
                        event.reason
                    ),
                ],
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    pub fn query(
        &mut self,
        query: &ExecutionAuditQuery,
    ) -> Result<Vec<ExecutionAuditEvent>, String> {
        let connection =
            rusqlite::Connection::open(&self.path).map_err(|error| error.to_string())?;
        let table_exists: bool = connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_events')",
                [],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if !table_exists {
            return Ok(Vec::new());
        }
        let mut statement = connection
            .prepare(
                "SELECT sequence, order_id, status, venue_order_id, occurred_at_unix_nanos, reason
                 FROM execution_events
                 WHERE (?1 IS NULL OR order_id = ?1)
                   AND (?2 IS NULL OR venue_order_id = ?2)
                   AND (?3 IS NULL OR lower(status) = lower(?3))
                   AND (?4 IS NULL OR occurred_at_unix_nanos >= ?4)
                   AND (?5 IS NULL OR occurred_at_unix_nanos <= ?5)
                 ORDER BY sequence ASC
                 LIMIT ?6",
            )
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map(
                rusqlite::params![
                    query.order_id,
                    query.venue_order_id,
                    query.status,
                    query.since_unix_nanos.map(|value| value as i64),
                    query.until_unix_nanos.map(|value| value as i64),
                    query.limit.unwrap_or(10_000) as i64,
                ],
                |row| {
                    Ok(ExecutionAuditEvent {
                        sequence: row.get::<_, i64>(0)? as u64,
                        order_id: row.get(1)?,
                        status: row.get(2)?,
                        venue_order_id: row.get(3)?,
                        occurred_at_unix_nanos: row.get::<_, i64>(4)? as u64,
                        reason: row.get(5)?,
                    })
                },
            )
            .map_err(|error| error.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())
    }
}

impl FileExecutionStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl SqliteExecutionStore {
    pub fn new(path: impl AsRef<std::path::Path>) -> Result<Self, String> {
        if let Some(parent) = path.as_ref().parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let connection = rusqlite::Connection::open(path).map_err(|error| error.to_string())?;
        connection
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 PRAGMA synchronous = NORMAL;
                 PRAGMA busy_timeout = 5000;
                 CREATE TABLE IF NOT EXISTS execution_checkpoints (
                    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation INTEGER NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    payload BLOB NOT NULL,
                    created_at_unix_nanos INTEGER NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS execution_checkpoints_sequence
                    ON execution_checkpoints(event_sequence);
                 CREATE TABLE IF NOT EXISTS execution_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    event_kind TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    created_at_unix_nanos INTEGER NOT NULL
                 );
                 CREATE INDEX IF NOT EXISTS execution_outbox_id
                    ON execution_outbox(outbox_id);",
            )
            .map_err(|error| error.to_string())?;
        Ok(Self { connection })
    }

    pub fn compact(&mut self, keep_latest: u32) -> Result<(), String> {
        let keep_latest = i64::from(keep_latest.max(1));
        self.connection
            .execute(
                "DELETE FROM execution_checkpoints
                 WHERE checkpoint_id NOT IN (
                   SELECT checkpoint_id FROM execution_checkpoints
                   ORDER BY checkpoint_id DESC LIMIT ?1
                 )",
                [keep_latest],
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn insert_checkpoint(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        let payload = serde_json::to_vec(snapshot).map_err(|error| error.to_string())?;
        self.connection
            .execute(
                "INSERT INTO execution_checkpoints
                 (generation, event_sequence, payload, created_at_unix_nanos)
                 VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![
                    snapshot.generation as i64,
                    snapshot.event_sequence as i64,
                    payload,
                    now_nanos() as i64,
                ],
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn insert_outbox<T: serde::Serialize>(&mut self, kind: &str, event: &T) -> Result<(), String> {
        let payload = serde_json::to_vec(event).map_err(|error| error.to_string())?;
        let event_key = String::from_utf8_lossy(&payload).into_owned();
        self.connection
            .execute(
                "INSERT OR IGNORE INTO execution_outbox
                 (event_key, event_kind, payload, created_at_unix_nanos)
                 VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![event_key, kind, payload, now_nanos() as i64],
            )
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn commit_transaction<T: serde::Serialize>(
        &mut self,
        kind: &str,
        event: &T,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.connection
            .execute_batch("BEGIN IMMEDIATE")
            .map_err(|error| error.to_string())?;
        let result = (|| {
            self.insert_outbox(kind, event)?;
            self.insert_checkpoint(snapshot)?;
            Ok::<_, String>(())
        })();
        match result {
            Ok(()) => self
                .connection
                .execute_batch("COMMIT")
                .map_err(|error| error.to_string()),
            Err(error) => {
                let _ = self.connection.execute_batch("ROLLBACK");
                Err(error)
            }
        }
    }
}

impl ExecutionStateStore for SqliteExecutionStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        let payload = self.connection.query_row(
            "SELECT payload FROM execution_checkpoints
                 ORDER BY checkpoint_id DESC LIMIT 1",
            [],
            |row| row.get::<_, Vec<u8>>(0),
        );
        match payload {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map(Some)
                .map_err(|error| error.to_string()),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        self.insert_checkpoint(snapshot)?;
        if snapshot.event_sequence % 128 == 0 {
            self.compact(2)?;
        }
        Ok(())
    }

    fn commit_event(
        &mut self,
        event: &ExecutionEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.commit_transaction("order", event, snapshot)
    }

    fn commit_intent_event(
        &mut self,
        event: &IntentEvent,
        snapshot: &ExecutionSnapshot,
    ) -> Result<(), String> {
        self.commit_transaction("intent", event, snapshot)
    }

    fn append_event(&mut self, event: &ExecutionEvent) -> Result<(), String> {
        self.insert_outbox("order", event)
    }

    fn append_intent_event(&mut self, event: &IntentEvent) -> Result<(), String> {
        self.insert_outbox("intent", event)
    }

    fn pending_outbox(&mut self, limit: u32) -> Result<Vec<ExecutionOutboxEntry>, String> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT outbox_id, event_kind, payload
                 FROM execution_outbox ORDER BY outbox_id ASC LIMIT ?1",
            )
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map([limit.max(1) as i64], |row| {
                let id = row.get::<_, i64>(0)? as u64;
                let kind: String = row.get(1)?;
                let payload: Vec<u8> = row.get(2)?;
                Ok((id, kind, payload))
            })
            .map_err(|error| error.to_string())?;
        rows.map(|row| {
            let (id, kind, payload) = row.map_err(|error| error.to_string())?;
            let event = match kind.as_str() {
                "order" => ExecutionOutboxEvent::Order(
                    serde_json::from_slice(&payload).map_err(|error| error.to_string())?,
                ),
                "intent" => ExecutionOutboxEvent::Intent(
                    serde_json::from_slice(&payload).map_err(|error| error.to_string())?,
                ),
                other => return Err(format!("unknown execution outbox event kind: {other}")),
            };
            Ok(ExecutionOutboxEntry { id, event })
        })
        .collect()
    }

    fn acknowledge_outbox(&mut self, ids: &[u64]) -> Result<(), String> {
        if ids.is_empty() {
            return Ok(());
        }
        self.connection
            .execute_batch("BEGIN IMMEDIATE")
            .map_err(|error| error.to_string())?;
        let result = (|| {
            let mut statement = self
                .connection
                .prepare("DELETE FROM execution_outbox WHERE outbox_id = ?1")
                .map_err(|error| error.to_string())?;
            for id in ids {
                statement
                    .execute([*id as i64])
                    .map_err(|error| error.to_string())?;
            }
            Ok::<_, String>(())
        })();
        match result {
            Ok(()) => self
                .connection
                .execute_batch("COMMIT")
                .map_err(|error| error.to_string()),
            Err(error) => {
                let _ = self.connection.execute_batch("ROLLBACK");
                Err(error)
            }
        }
    }
}

impl ExecutionStateStore for FileExecutionStore {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String> {
        match std::fs::read(&self.path) {
            Ok(bytes) => serde_json::from_slice(&bytes)
                .map(Some)
                .map_err(|error| error.to_string()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    }

    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.path.with_extension("tmp");
        let bytes = serde_json::to_vec_pretty(snapshot).map_err(|error| error.to_string())?;
        std::fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
        std::fs::rename(&temporary, &self.path).map_err(|error| error.to_string())
    }
}
mod preflight;

pub use preflight::{QueuedExecutionPreflight, SocketExecutionPreflight};
