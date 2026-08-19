//! Typed dependency projections used by Execution planning and admission.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, RwLock,
};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use kairos_account_contract::{
    AccountContractClient, AccountViewKey, AccountViewKind, AccountViewReader,
    DecimalValue as AccountDecimal, Health,
};
use kairos_protocol::generated::kairos::{
    account::v_2::{AccountStatus, FreshnessState},
    common::v_2::ViewCompleteness,
};
use kairos_reference_contract::{ReferenceHealth, ReferenceMarket};
use kairos_risk_contract::{Health as RiskHealth, RiskControlClient};

#[derive(Clone)]
pub(super) struct AccountProjection {
    pub(super) health: Health,
    pub(super) balances: Vec<ProjectedBalance>,
    pub(super) positions: Vec<ProjectedPosition>,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone)]
pub(super) struct ProjectedBalance {
    pub(super) asset_code: String,
    pub(super) available: Option<AccountDecimal>,
}

#[derive(Clone)]
pub(super) struct ProjectedPosition {
    pub(super) instrument_id: String,
    pub(super) quantity: AccountDecimal,
}

#[derive(Clone)]
pub(super) struct MarketProjection {
    pub(super) generation: u64,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone)]
pub(super) struct ReferenceProjection {
    pub(super) health: ReferenceHealth,
    pub(super) markets: Vec<ReferenceMarket>,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone)]
pub(super) struct RiskProjection {
    pub(super) health: RiskHealth,
}

#[derive(Default)]
pub(super) struct DependencyProjection {
    pub(super) accounts: BTreeMap<String, AccountProjection>,
    pub(super) market: Option<MarketProjection>,
    pub(super) reference: Option<ReferenceProjection>,
    pub(super) risk: Option<RiskProjection>,
}

const PROJECTION_REFRESH: Duration = Duration::from_millis(250);
const PROJECTION_MAX_AGE: Duration = Duration::from_secs(5);

pub(super) struct DependencyProjectionRuntime {
    state: Arc<RwLock<DependencyProjection>>,
    stop: Arc<AtomicBool>,
    workers: Vec<JoinHandle<()>>,
}

impl DependencyProjectionRuntime {
    pub(super) fn start(
        accounts: &BTreeMap<String, PathBuf>,
        account_snapshots: &BTreeMap<String, PathBuf>,
        market_snapshot: Option<&Path>,
        reference_database: Option<PathBuf>,
        reference_actor_id: Option<String>,
        risk: Option<PathBuf>,
    ) -> Self {
        let state = Arc::new(RwLock::new(DependencyProjection::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let mut workers = Vec::new();
        for (account_id, socket) in accounts {
            let Some(view_root) = account_snapshots.get(account_id).cloned() else {
                continue;
            };
            let account_id = account_id.clone();
            let socket = socket.clone();
            let state = Arc::clone(&state);
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
                let reader = loop {
                    let key = AccountViewKey::new(
                        format!("account:{account_id}"),
                        &account_id,
                        AccountViewKind::Current,
                    );
                    match key.and_then(|key| AccountViewReader::open(&view_root, key)) {
                        Ok(reader) => break reader,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(PROJECTION_REFRESH)
                        }
                        Err(_) => return,
                    }
                };
                while !stop.load(Ordering::Acquire) {
                    let result = client
                        .health()
                        .map_err(|error| error.to_string())
                        .and_then(|health| read_account_projection(&reader, &account_id, health));
                    if let Ok(value) = result {
                        if let Ok(mut projection) = state.write() {
                            projection.accounts.insert(account_id.clone(), value);
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if market_snapshot.is_some() {
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                while !stop.load(Ordering::Acquire) {
                    if let Ok(mut projection) = state.write() {
                        if let Some(value) = projection.market.as_mut() {
                            value.refreshed_at = Instant::now();
                        } else {
                            projection.market = Some(MarketProjection {
                                generation: 0,
                                refreshed_at: Instant::now(),
                            });
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if let (Some(database), Some(actor_id)) = (reference_database, reference_actor_id) {
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let mut last_watermark = None;
                while !stop.load(Ordering::Acquire) {
                    let result = read_reference_projection(&database, &actor_id).map(|value| {
                        let watermark = (value.health.generation, value.health.event_sequence);
                        (value, watermark)
                    });
                    if let Ok((value, watermark)) = result {
                        if let Ok(mut projection) = state.write() {
                            if last_watermark == Some(watermark) {
                                if let Some(current) = projection.reference.as_mut() {
                                    current.health = value.health;
                                    current.refreshed_at = Instant::now();
                                }
                            } else {
                                last_watermark = Some(watermark);
                                projection.reference = Some(value);
                            }
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        if let Some(path) = risk {
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let client = loop {
                    match RiskControlClient::connect(&path) {
                        Ok(client) => break client,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(PROJECTION_REFRESH)
                        }
                        Err(_) => return,
                    }
                };
                while !stop.load(Ordering::Acquire) {
                    if let Ok(health) = client.health() {
                        if let Ok(mut projection) = state.write() {
                            projection.risk = Some(RiskProjection { health });
                        }
                    }
                    std::thread::sleep(PROJECTION_REFRESH);
                }
            }));
        }
        Self {
            state,
            stop,
            workers,
        }
    }

    pub(super) fn account(&self, account_id: &str) -> Result<AccountProjection, String> {
        let value = self
            .state
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

    pub(super) fn reference(&self) -> Result<ReferenceProjection, String> {
        let value = self
            .state
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

    pub(super) fn refresh_accounts(
        &self,
        accounts: &BTreeMap<String, PathBuf>,
        snapshots: &BTreeMap<String, PathBuf>,
    ) -> Result<(), String> {
        for (account_id, socket) in accounts {
            let health = AccountContractClient::connect(socket)
                .and_then(|client| client.health())
                .map_err(|error| error.to_string())?;
            let view_root = snapshots
                .get(account_id)
                .ok_or_else(|| format!("account view root is not bound: {account_id}"))?;
            let key = AccountViewKey::new(
                format!("account:{account_id}"),
                account_id,
                AccountViewKind::Current,
            )
            .map_err(|error| error.to_string())?;
            let reader =
                AccountViewReader::open(view_root, key).map_err(|error| error.to_string())?;
            let value = read_account_projection(&reader, account_id, health)?;
            self.state
                .write()
                .map_err(|_| "account projection lock poisoned".to_string())?
                .accounts
                .insert(account_id.clone(), value);
        }
        Ok(())
    }

    pub(super) fn watermarks(&self) -> crate::application::DependencyWatermarks {
        let Ok(state) = self.state.read() else {
            return Default::default();
        };
        crate::application::DependencyWatermarks {
            account: state
                .accounts
                .iter()
                .filter(|(_, value)| value.refreshed_at.elapsed() <= PROJECTION_MAX_AGE)
                .map(|(account_id, value)| {
                    (
                        account_id.clone(),
                        crate::application::SnapshotWatermark {
                            generation: value.health.generation.into(),
                            event_sequence: value.health.event_sequence.into(),
                        },
                    )
                })
                .collect(),
            market: state
                .market
                .as_ref()
                .filter(|value| value.refreshed_at.elapsed() <= PROJECTION_MAX_AGE)
                .map(|value| crate::application::SnapshotWatermark {
                    generation: value.generation.into(),
                    event_sequence: 0.into(),
                }),
            reference: state.reference.as_ref().map(|value| {
                crate::application::SnapshotWatermark {
                    generation: value.health.generation.into(),
                    event_sequence: value.health.event_sequence.into(),
                }
            }),
            risk: state
                .risk
                .as_ref()
                .map(|value| crate::application::SnapshotWatermark {
                    generation: value.health.generation.into(),
                    event_sequence: value.health.event_sequence.into(),
                }),
        }
    }
}

impl Drop for DependencyProjectionRuntime {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

pub(super) fn read_reference_projection(
    database: &Path,
    actor_id: &str,
) -> Result<ReferenceProjection, String> {
    let client = kairos_reference_contract::ReferenceClient::connect(
        kairos_reference_contract::ReferenceEndpoint {
            database: database.to_path_buf(),
            actor_id: actor_id.to_owned(),
            events: kairos_transport::AeronEndpoint::from_parts(
                None,
                kairos_transport::DEFAULT_CHANNEL,
                kairos_transport::stream_ids::REFERENCE_CHANGES,
            )
            .map_err(|error| error.to_string())?,
        },
    );
    let snapshot = client
        .execution_snapshot()
        .map_err(|error| error.to_string())?;
    let markets = snapshot
        .markets
        .into_iter()
        .map(|value| ReferenceMarket {
            market_id: value.market_id,
            instrument_id: value.instrument_id,
            listing_id: value.listing_id,
            exchange_id: value.exchange_id,
            instrument_kind: value.instrument_kind.to_string(),
            asset_type: value.asset_type.map(|item| item.to_string()),
            venue_symbol: value.venue_symbol,
            base_asset_id: value.base_asset_id,
            quote_asset_id: value.quote_asset_id,
            underlying_instrument_id: value.underlying_instrument_id,
            status: value.status,
            price_tick: value.price_tick,
            quantity_tick: value.quantity_tick,
            minimum_quantity: value.minimum_quantity,
            minimum_notional: value.minimum_notional,
            price_precision: value.price_precision,
            quantity_precision: value.quantity_precision,
            contract_size: value.contract_size,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos,
        })
        .collect();
    Ok(ReferenceProjection {
        health: ReferenceHealth {
            status: "ready".into(),
            generation: snapshot.generation,
            event_sequence: snapshot.event_sequence,
        },
        markets,
        refreshed_at: Instant::now(),
    })
}

pub(super) fn read_account_projection(
    reader: &AccountViewReader,
    account_id: &str,
    health: Health,
) -> Result<AccountProjection, String> {
    let frame = reader.read().map_err(|error| error.to_string())?;
    let view = frame.account_current().map_err(|error| error.to_string())?;
    let metadata = view.metadata();
    if frame.generation() != metadata.generation()
        || metadata.generation() != health.generation
        || metadata.applied_revision() != Some(health.event_sequence)
    {
        return Err("Account health and mmap watermark disagree".into());
    }
    if metadata.completeness() != ViewCompleteness::COMPLETE || view.account_id() != account_id {
        return Err("Account mmap identity or completeness is invalid".into());
    }
    let mut balances = Vec::new();
    let mut positions = Vec::new();
    for segment in view.segments() {
        if segment.freshness() != FreshnessState::FRESH || segment.status() != AccountStatus::ACTIVE
        {
            return Err(format!(
                "Account segment is not ready: {}",
                segment.segment_key()
            ));
        }
        balances.extend(segment.balances().iter().map(|balance| ProjectedBalance {
            asset_code: balance.asset_code().unwrap_or_default().to_owned(),
            available: balance.available().map(|value| {
                AccountDecimal::new(value.mantissa(), value.scale())
                    .expect("Account balance satisfies contract decimal bounds")
            }),
        }));
        positions.extend(segment.positions().iter().map(|position| {
            let quantity = position.quantity();
            ProjectedPosition {
                instrument_id: position.instrument_id().to_owned(),
                quantity: AccountDecimal::new(quantity.mantissa(), quantity.scale())
                    .expect("Account position satisfies contract decimal bounds"),
            }
        }));
    }
    Ok(AccountProjection {
        health,
        balances,
        positions,
        refreshed_at: Instant::now(),
    })
}
