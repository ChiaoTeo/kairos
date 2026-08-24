//! Typed dependency state used by Execution planning and admission.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use kairos_account_contract::{
    AccountClient, AccountControlRpcClient, AccountCurrent, DecimalValue as AccountDecimal, Health,
    ObservedOrders,
};
use kairos_primitives::account::PositionSide;
use kairos_primitives::execution::OrderId;
use kairos_primitives::time::Sequence;
use kairos_protocol::generated::kairos::account::v_2::{
    AccountStatus, FreshnessState, PositionSide as AccountPositionSide,
};
use kairos_protocol::generated::kairos::common::v_2::ViewCompleteness;
use kairos_reference_contract::Market;
use kairos_risk_contract::{Health as RiskHealth, RiskControlRpcClient};

#[derive(Clone)]
pub(super) struct AccountDependencyState {
    pub(super) health: Health,
    pub(super) balances: Vec<AccountBalanceFact>,
    pub(super) positions: Vec<AccountPositionFact>,
    pub(super) commitment_observation: AccountCommitmentObservation,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct AccountCommitmentObservation {
    pub(crate) account_id: String,
    pub(crate) watermark: Sequence,
    pub(crate) observed_order_ids: BTreeSet<OrderId>,
}

#[derive(Clone)]
pub(super) struct AccountBalanceFact {
    pub(super) asset_code: String,
    pub(super) available: Option<AccountDecimal>,
}

#[derive(Clone)]
pub(super) struct AccountPositionFact {
    pub(super) segment_key: String,
    pub(super) instrument_id: String,
    pub(super) position_side: PositionSide,
    pub(super) quantity: AccountDecimal,
}

#[derive(Clone)]
pub(super) struct MarketDependencyState {
    pub(super) generation: u64,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone)]
pub(super) struct ReferenceDependencyState {
    pub(super) generation: kairos_primitives::time::Generation,
    pub(super) event_sequence: Sequence,
    pub(super) markets: Vec<Market>,
    pub(super) refreshed_at: Instant,
}

#[derive(Clone)]
pub(super) struct RiskDependencyState {
    pub(super) health: RiskHealth,
}

#[derive(Default)]
pub(super) struct DependencyState {
    pub(super) accounts: BTreeMap<String, AccountDependencyState>,
    pub(super) market: Option<MarketDependencyState>,
    pub(super) reference: Option<ReferenceDependencyState>,
    pub(super) risk: Option<RiskDependencyState>,
}

const DEPENDENCY_REFRESH_INTERVAL: Duration = Duration::from_millis(250);
const DEPENDENCY_MAX_AGE: Duration = Duration::from_secs(5);

pub(super) struct DependencyStateRuntime {
    state: Arc<RwLock<DependencyState>>,
    stop: Arc<AtomicBool>,
    workers: Vec<JoinHandle<()>>,
}

impl DependencyStateRuntime {
    pub(super) fn start(
        accounts: &BTreeMap<String, AccountClient>,
        market_snapshot: Option<&Path>,
        reference: Option<ReferenceDependencyState>,
        risk: Option<kairos_risk_contract::RiskClient>,
    ) -> Self {
        let state = Arc::new(RwLock::new(DependencyState::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let mut workers = Vec::new();
        for (account_id, client) in accounts {
            let account_id = account_id.clone();
            let client = client.clone();
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let runtime = match tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    Ok(runtime) => runtime,
                    Err(_) => return,
                };
                let reader = loop {
                    match client.account_current(format!("account:{account_id}"), &account_id) {
                        Ok(reader) => break reader,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL)
                        },
                        Err(_) => return,
                    }
                };
                let observed_orders_reader = loop {
                    match client.observed_orders(format!("account:{account_id}"), &account_id) {
                        Ok(reader) => break reader,
                        Err(_) if !stop.load(Ordering::Acquire) => {
                            std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL)
                        },
                        Err(_) => return,
                    }
                };
                while !stop.load(Ordering::Acquire) {
                    let result = runtime
                        .block_on(AccountControlRpcClient::health(&client.control()))
                        .map_err(|error| error.to_string())
                        .and_then(|health| {
                            read_account_dependency_state(
                                &reader,
                                &observed_orders_reader,
                                &account_id,
                                health,
                            )
                        });
                    if let Ok(value) = result {
                        if let Ok(mut state) = state.write() {
                            state.accounts.insert(account_id.clone(), value);
                        }
                    }
                    std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL);
                }
            }));
        }
        if market_snapshot.is_some() {
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                while !stop.load(Ordering::Acquire) {
                    if let Ok(mut state) = state.write() {
                        if let Some(value) = state.market.as_mut() {
                            value.refreshed_at = Instant::now();
                        } else {
                            state.market = Some(MarketDependencyState {
                                generation: 0,
                                refreshed_at: Instant::now(),
                            });
                        }
                    }
                    std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL);
                }
            }));
        }
        if let Some(reference) = reference {
            if let Ok(mut state) = state.write() {
                state.reference = Some(reference);
            }
        }
        if let Some(endpoint) = risk {
            let state = Arc::clone(&state);
            let stop = Arc::clone(&stop);
            workers.push(std::thread::spawn(move || {
                let runtime = match tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    Ok(runtime) => runtime,
                    Err(_) => return,
                };
                while !stop.load(Ordering::Acquire) {
                    if let Ok(health) =
                        runtime.block_on(RiskControlRpcClient::health(&endpoint.control()))
                    {
                        if let Ok(mut state) = state.write() {
                            state.risk = Some(RiskDependencyState { health });
                        }
                    } else {
                        std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL);
                    }
                    std::thread::sleep(DEPENDENCY_REFRESH_INTERVAL);
                }
            }));
        }
        Self {
            state,
            stop,
            workers,
        }
    }

    pub(super) fn account(&self, account_id: &str) -> Result<AccountDependencyState, String> {
        let value = self
            .state
            .read()
            .map_err(|_| "account state lock poisoned".to_string())?
            .accounts
            .get(account_id)
            .cloned()
            .ok_or_else(|| format!("account state is not ready: {account_id}"))?;
        if value.refreshed_at.elapsed() > DEPENDENCY_MAX_AGE {
            return Err(format!("account state is stale: {account_id}"));
        }
        Ok(value)
    }

    pub(super) fn reference(&self) -> Result<ReferenceDependencyState, String> {
        let mut guard = self
            .state
            .write()
            .map_err(|_| "reference state lock poisoned".to_string())?;
        let value = guard
            .reference
            .as_mut()
            .ok_or_else(|| "reference state is not ready".to_string())?;
        value.refreshed_at = Instant::now();
        Ok(value.clone())
    }

    pub(super) fn refresh_accounts(
        &self,
        accounts: &BTreeMap<String, AccountClient>,
    ) -> Result<(), String> {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| error.to_string())?;
        for (account_id, client) in accounts {
            let health = runtime
                .block_on(AccountControlRpcClient::health(&client.control()))
                .map_err(|error| error.to_string())?;
            let reader = client
                .account_current(format!("account:{account_id}"), account_id)
                .map_err(|error| error.to_string())?;
            let observed_orders_reader = client
                .observed_orders(format!("account:{account_id}"), account_id)
                .map_err(|error| error.to_string())?;
            let value = read_account_dependency_state(
                &reader,
                &observed_orders_reader,
                account_id,
                health,
            )?;
            self.state
                .write()
                .map_err(|_| "account state lock poisoned".to_string())?
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
                .filter(|(_, value)| value.refreshed_at.elapsed() <= DEPENDENCY_MAX_AGE)
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
                .filter(|value| value.refreshed_at.elapsed() <= DEPENDENCY_MAX_AGE)
                .map(|value| crate::application::SnapshotWatermark {
                    generation: value.generation.into(),
                    event_sequence: 0.into(),
                }),
            reference: state.reference.as_ref().map(|value| {
                crate::application::SnapshotWatermark {
                    generation: value.generation.into(),
                    event_sequence: value.event_sequence.into(),
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

impl Drop for DependencyStateRuntime {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        for worker in self.workers.drain(..) {
            let _ = worker.join();
        }
    }
}

pub(super) fn reference_dependency_state(
    snapshot: kairos_reference_contract::ExecutionReferenceSnapshot,
) -> ReferenceDependencyState {
    let markets = snapshot
        .markets
        .into_iter()
        .map(|value| Market {
            market_id: value.market_id,
            instrument_id: value.instrument_id,
            listing_id: value.listing_id,
            exchange_id: value.exchange_id,
            instrument_kind: value.instrument_kind,
            asset_type: value.asset_type,
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
    ReferenceDependencyState {
        generation: snapshot.generation,
        event_sequence: snapshot.event_sequence,
        markets,
        refreshed_at: Instant::now(),
    }
}

pub(super) fn read_account_dependency_state(
    reader: &AccountCurrent,
    observed_orders_reader: &ObservedOrders,
    account_id: &str,
    health: Health,
) -> Result<AccountDependencyState, String> {
    let snapshot = reader.read().map_err(|error| error.to_string())?;
    let view = snapshot.view().map_err(|error| error.to_string())?;
    let metadata = view.metadata();
    if snapshot.generation() != metadata.generation()
        || metadata.generation() != health.generation.get()
        || metadata.applied_revision() != Some(health.event_sequence.get())
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
        balances.extend(segment.balances().iter().map(|balance| AccountBalanceFact {
            asset_code: balance.asset_code().unwrap_or_default().to_owned(),
            available: balance.available().map(|value| {
                AccountDecimal::new(value.mantissa(), value.scale())
                    .expect("Account balance satisfies contract decimal bounds")
            }),
        }));
        positions.extend(segment.positions().iter().map(|position| {
            let quantity = position.quantity();
            AccountPositionFact {
                segment_key: segment.segment_key().to_owned(),
                instrument_id: position.instrument_id().to_owned(),
                position_side: match position.position_side() {
                    AccountPositionSide::LONG => PositionSide::Long,
                    AccountPositionSide::SHORT => PositionSide::Short,
                    _ => PositionSide::Net,
                },
                quantity: AccountDecimal::new(quantity.mantissa(), quantity.scale())
                    .expect("Account position satisfies contract decimal bounds"),
            }
        }));
    }
    let observed_snapshot = observed_orders_reader
        .read()
        .map_err(|error| error.to_string())?;
    let observed_view = observed_snapshot
        .view()
        .map_err(|error| error.to_string())?;
    let observed_metadata = observed_view.metadata();
    if observed_snapshot.generation() != observed_metadata.generation()
        || observed_metadata.generation() != health.generation.get()
        || observed_metadata.applied_revision() != Some(health.event_sequence.get())
        || observed_metadata.completeness() != ViewCompleteness::COMPLETE
        || observed_view.account_id() != account_id
    {
        return Err(
            "Account observed-orders watermark, identity, or completeness is invalid".into(),
        );
    }
    let observed_order_ids = observed_view
        .segments()
        .iter()
        .flat_map(|segment| segment.orders().iter())
        .filter_map(|order| order.execution_order_id())
        .filter(|value| !value.is_empty())
        .map(OrderId::new)
        .collect::<Result<BTreeSet<_>, _>>()
        .map_err(|error| error.to_string())?;
    Ok(AccountDependencyState {
        health,
        balances,
        positions,
        commitment_observation: AccountCommitmentObservation {
            account_id: account_id.to_owned(),
            watermark: Sequence::new(observed_snapshot.envelope_metadata().applied_event_sequence),
            observed_order_ids,
        },
        refreshed_at: Instant::now(),
    })
}
