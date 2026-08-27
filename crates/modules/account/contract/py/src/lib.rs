use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;

use kairos_account_contract::{AccountIndexedView as RustView, ContractError};
use kairos_primitives::account::AccountId;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

create_exception!(
    _native_account_contract,
    AccountInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_account_contract,
    AccountCurrentViewUnavailableError,
    PyRuntimeError
);

const API_VERSION: u32 = 1;

#[pyclass(frozen, module = "kairospy._native_account_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountBalanceCurrent {
    #[pyo3(get)]
    asset: String,
    #[pyo3(get)]
    total: NativeDecimal,
    #[pyo3(get)]
    available: NativeDecimal,
    #[pyo3(get)]
    reserved: NativeDecimal,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountCollateralCurrent {
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    asset: String,
    #[pyo3(get)]
    total: NativeDecimal,
    #[pyo3(get)]
    available: Option<NativeDecimal>,
    #[pyo3(get)]
    locked: Option<NativeDecimal>,
    #[pyo3(get)]
    borrowed: Option<NativeDecimal>,
    #[pyo3(get)]
    interest: Option<NativeDecimal>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountPositionCurrent {
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    position_side: String,
    #[pyo3(get)]
    average_price: Option<NativeDecimal>,
    #[pyo3(get)]
    market_value: Option<NativeDecimal>,
    #[pyo3(get)]
    unrealized_pnl: Option<NativeDecimal>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountEarnHoldingCurrent {
    #[pyo3(get)]
    holding_key: String,
    #[pyo3(get)]
    participant_position_id: Option<String>,
    #[pyo3(get)]
    product_id: String,
    #[pyo3(get)]
    asset: String,
    #[pyo3(get)]
    principal: NativeDecimal,
    #[pyo3(get)]
    redeemable: Option<NativeDecimal>,
    #[pyo3(get)]
    state: String,
    #[pyo3(get)]
    participant_state: Option<String>,
    #[pyo3(get)]
    liquidity: String,
    #[pyo3(get)]
    notice_seconds: Option<u64>,
    #[pyo3(get)]
    matures_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    observed_at_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountSegmentCurrent {
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    broker: String,
    #[pyo3(get)]
    environment: String,
    #[pyo3(get)]
    account_model: Option<String>,
    #[pyo3(get)]
    equity: Option<NativeDecimal>,
    #[pyo3(get)]
    balances: Vec<AccountBalanceCurrent>,
    #[pyo3(get)]
    positions: Vec<AccountPositionCurrent>,
    #[pyo3(get)]
    earn_holdings: Vec<AccountEarnHoldingCurrent>,
    #[pyo3(get)]
    earn_watermark_unix_nanos: Option<u64>,
    #[pyo3(get)]
    freshness: String,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    sync_mode: String,
    #[pyo3(get)]
    sync_lifecycle: String,
    #[pyo3(get)]
    completeness: String,
    #[pyo3(get)]
    snapshot_watermark: Option<u64>,
    #[pyo3(get)]
    event_watermark: Option<u64>,
    #[pyo3(get)]
    channel_epoch: Option<u64>,
    #[pyo3(get)]
    last_event_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    last_success_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    last_error: Option<String>,
    #[pyo3(get)]
    recovery_buffer_depth: u64,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountObservedOrderCurrent {
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    observation_id: Option<String>,
    #[pyo3(get)]
    source_id: Option<String>,
    #[pyo3(get)]
    execution_order_id: Option<String>,
    #[pyo3(get)]
    remote_order_id: Option<String>,
    #[pyo3(get)]
    instrument_id: Option<String>,
    #[pyo3(get)]
    market_id: Option<String>,
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    filled_quantity: NativeDecimal,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    observed_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
struct AccountCurrentSnapshot {
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segments: Vec<AccountSegmentCurrent>,
    #[pyo3(get)]
    collateral: Vec<AccountCollateralCurrent>,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
    #[pyo3(get)]
    observed_orders: Vec<AccountObservedOrderCurrent>,
}

#[pyclass(module = "kairospy._native_account_contract")]
struct AccountCurrentView {
    creator_pid: u32,
    reader: Mutex<Option<RustView>>,
}

#[pymethods]
impl AccountCurrentView {
    #[new]
    #[pyo3(signature = (root, account_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        py: Python<'_>,
        root: PathBuf,
        account_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let account_id =
            AccountId::new(account_id).map_err(|error| PyValueError::new_err(error.to_string()))?;
        let reader = py
            .detach(move || RustView::open(root, &identity, account_id))
            .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            reader: Mutex::new(Some(reader)),
        })
    }

    fn snapshot(&self, py: Python<'_>) -> PyResult<AccountCurrentSnapshot> {
        self.ensure_process()?;
        py.detach(|| {
            let reader = self.reader.lock().map_err(|_| lock_error())?;
            let snapshot = reader
                .as_ref()
                .ok_or_else(|| PyRuntimeError::new_err("Account current-view reader is closed"))?
                .snapshot()
                .map_err(contract_error)?;
            project_snapshot(snapshot).map_err(contract_error)
        })
    }

    fn close(&self) -> PyResult<()> {
        self.ensure_process()?;
        self.reader.lock().map_err(|_| lock_error())?.take();
        Ok(())
    }
    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }
    fn __exit__(
        &self,
        _ty: &Bound<'_, PyAny>,
        _value: &Bound<'_, PyAny>,
        _traceback: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        self.close()?;
        Ok(false)
    }
}

impl AccountCurrentView {
    fn ensure_process(&self) -> PyResult<()> {
        if std::process::id() != self.creator_pid {
            Err(PyRuntimeError::new_err(
                "Account current-view reader cannot be used after fork",
            ))
        } else {
            Ok(())
        }
    }
}

fn project_snapshot(
    snapshot: kairos_account_contract::AccountIndexedSnapshot,
) -> Result<AccountCurrentSnapshot, ContractError> {
    let event_sequence = snapshot.metadata().applied_event_sequence;
    let mut balances: BTreeMap<String, Vec<AccountBalanceCurrent>> = BTreeMap::new();
    for value in snapshot.balances() {
        let root = value.balance()?;
        let raw = root.balance();
        balances
            .entry(root.segment_key().to_owned())
            .or_default()
            .push(AccountBalanceCurrent {
                asset: raw
                    .asset_code()
                    .filter(|value| !value.is_empty())
                    .unwrap_or(raw.asset_id())
                    .to_owned(),
                total: decimal(raw.total()),
                available: raw.available().map(decimal).unwrap_or_else(zero_decimal),
                reserved: raw.locked().map(decimal).unwrap_or_else(zero_decimal),
            });
    }
    let mut collateral = Vec::new();
    for value in snapshot.collateral() {
        let root = value.collateral()?;
        let raw = root.balance();
        collateral.push(AccountCollateralCurrent {
            account_id: root.account_id().to_owned(),
            segment_key: root.segment_key().to_owned(),
            asset: raw
                .asset_code()
                .filter(|value| !value.is_empty())
                .unwrap_or(raw.asset_id())
                .to_owned(),
            total: decimal(raw.total()),
            available: raw.available().map(decimal),
            locked: raw.locked().map(decimal),
            borrowed: raw.borrowed().map(decimal),
            interest: raw.interest().map(decimal),
        });
    }
    let mut positions: BTreeMap<String, Vec<AccountPositionCurrent>> = BTreeMap::new();
    for value in snapshot.positions() {
        let root = value.position()?;
        let raw = root.position();
        let quantity = decimal(raw.quantity());
        let mark = raw.mark_price().map(decimal);
        positions
            .entry(root.segment_key().to_owned())
            .or_default()
            .push(AccountPositionCurrent {
                instrument_id: raw.instrument_id().to_owned(),
                quantity: quantity.clone(),
                position_side: position_side(raw.position_side().0).to_owned(),
                average_price: raw.average_price().map(decimal),
                market_value: mark.map(|mark| multiply(&quantity, &mark)),
                unrealized_pnl: raw.unrealized_pnl().map(decimal),
            });
    }
    let mut holdings: BTreeMap<String, Vec<AccountEarnHoldingCurrent>> = BTreeMap::new();
    for value in snapshot.earn_holdings() {
        let root = value.earn_holding()?;
        let raw = root.holding();
        holdings
            .entry(root.segment_key().to_owned())
            .or_default()
            .push(AccountEarnHoldingCurrent {
                holding_key: raw.holding_key().to_owned(),
                participant_position_id: optional_text(raw.participant_position_id()),
                product_id: raw.product_id().to_owned(),
                asset: raw.asset().to_owned(),
                principal: decimal(raw.principal()),
                redeemable: raw.redeemable().map(decimal),
                state: match raw.state().0 {
                    1 => "active",
                    2 => "redeeming",
                    3 => "redeemed",
                    _ => "unknown",
                }
                .to_owned(),
                participant_state: optional_text(raw.participant_state()),
                liquidity: match raw.liquidity().0 {
                    1 => "immediate",
                    2 => "notice",
                    3 => "fixed_term",
                    _ => "unknown",
                }
                .to_owned(),
                notice_seconds: optional_u64(raw.notice_seconds()),
                matures_at_unix_nanos: optional_u64(raw.matures_at_unix_nanos()),
                observed_at_unix_nanos: optional_u64(raw.observed_at_unix_nanos()),
            });
    }
    let mut valuations = BTreeMap::new();
    for value in snapshot.valuations() {
        let root = value.valuation()?;
        valuations.insert(
            root.segment_key().to_owned(),
            root.valuation().equity().map(decimal),
        );
    }
    let account_id = snapshot
        .segments()
        .first()
        .map(|value| value.segment().map(|root| root.account_id().to_owned()))
        .transpose()?
        .unwrap_or_default();
    let mut segments = Vec::new();
    for value in snapshot.segments() {
        let root = value.segment()?;
        let state = root.state();
        let key = state.segment_key().to_owned();
        segments.push(AccountSegmentCurrent {
            account_id: root.account_id().to_owned(),
            segment_key: key.clone(),
            broker: state.broker().to_owned(),
            environment: state.environment().to_owned(),
            account_model: match state.observed_account_model().0 {
                1 => Some("cash"),
                2 => Some("margin"),
                3 => Some("portfolio_margin"),
                _ => None,
            }
            .map(str::to_owned),
            equity: valuations.remove(&key).flatten(),
            balances: balances.remove(&key).unwrap_or_default(),
            positions: positions.remove(&key).unwrap_or_default(),
            earn_holdings: holdings.remove(&key).unwrap_or_default(),
            earn_watermark_unix_nanos: optional_u64(state.earn_watermark_unix_nanos()),
            freshness: match state.freshness().0 {
                1 => "fresh",
                2 => "stale",
                4 => "resyncing",
                5 => "unavailable",
                _ => "unknown",
            }
            .to_owned(),
            generation: state.state_generation(),
            sync_mode: match state.sync_mode().0 {
                1 => "snapshot_then_stream",
                2 => "snapshot_only",
                _ => "unknown",
            }
            .to_owned(),
            sync_lifecycle: match state.sync_lifecycle().0 {
                1 => "configured",
                2 => "bootstrapping",
                3 => "live",
                4 => "snapshot_current",
                5 => "degraded",
                6 => "resyncing",
                7 => "unavailable",
                8 => "stopped",
                _ => "configured",
            }
            .to_owned(),
            completeness: match state.completeness().0 {
                1 => "complete",
                2 => "partial",
                _ => "unknown",
            }
            .to_owned(),
            snapshot_watermark: optional_u64(state.snapshot_watermark()),
            event_watermark: optional_u64(state.event_watermark()),
            channel_epoch: optional_u64(state.channel_epoch()),
            last_event_at_unix_nanos: optional_u64(state.last_event_at_unix_nanos()),
            last_success_at_unix_nanos: optional_u64(state.last_success_at_unix_nanos()),
            last_error: optional_text(state.last_error()),
            recovery_buffer_depth: u64::from(state.recovery_buffer_depth()),
        });
    }
    let generation = segments
        .iter()
        .map(|segment| segment.generation)
        .max()
        .unwrap_or(0);
    let mut observed_orders = Vec::new();
    for value in snapshot.observed_orders() {
        let root = value.observed_order()?;
        let raw = root.order();
        observed_orders.push(AccountObservedOrderCurrent {
            segment_key: root.segment_key().to_owned(),
            observation_id: optional_required_text(raw.observation_id()),
            source_id: optional_required_text(raw.source_id()),
            execution_order_id: optional_text(raw.execution_order_id()),
            remote_order_id: optional_text(raw.remote_order_id()),
            instrument_id: optional_required_text(raw.instrument_id()),
            market_id: optional_required_text(raw.market_id()),
            side: match raw.side().0 {
                1 => "buy",
                2 => "sell",
                other => {
                    return Err(ContractError::Invalid(format!(
                        "unknown Account observed order side {other}"
                    )));
                },
            }
            .to_owned(),
            quantity: decimal(raw.quantity()),
            filled_quantity: decimal(raw.filled_quantity()),
            status: match raw.status().0 {
                1 => "open",
                2 => "partially_filled",
                3 => "pending_cancel",
                4 => "closed",
                5 => "unknown",
                other => {
                    return Err(ContractError::Invalid(format!(
                        "unknown Account observed order status {other}"
                    )));
                },
            }
            .to_owned(),
            observed_at_unix_nanos: raw.observed_at_unix_nanos(),
        });
    }
    Ok(AccountCurrentSnapshot {
        account_id,
        segments,
        collateral,
        generation,
        event_sequence,
        observed_orders,
    })
}

fn decimal(value: &Decimal64) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}
fn optional_text(value: Option<&str>) -> Option<String> {
    value
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
}
fn optional_required_text(value: &str) -> Option<String> {
    (!value.trim().is_empty()).then(|| value.to_owned())
}
fn optional_u64(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}
fn zero_decimal() -> NativeDecimal {
    NativeDecimal {
        mantissa: 0,
        scale: 0,
    }
}
fn position_side(value: u8) -> &'static str {
    match value {
        2 => "long",
        3 => "short",
        _ => "net",
    }
}
fn multiply(left: &NativeDecimal, right: &NativeDecimal) -> NativeDecimal {
    NativeDecimal {
        mantissa: left.mantissa.saturating_mul(right.mantissa),
        scale: left.scale.saturating_add(right.scale),
    }
}
fn identity(
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<InstanceIdentity> {
    match (launch_id, instance_id) {
        (Some(launch), Some(instance)) => InstanceIdentity::new(workspace_id, launch, instance)
            .map_err(|error| PyValueError::new_err(error.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| PyValueError::new_err(error.to_string())),
        _ => Err(PyValueError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        )),
    }
}
fn contract_error(error: ContractError) -> PyErr {
    match error {
        ContractError::Invalid(message) => AccountInvalidCurrentViewError::new_err(message),
        ContractError::Transport(message) | ContractError::Unsupported(message) => {
            AccountCurrentViewUnavailableError::new_err(message)
        },
    }
}
fn lock_error() -> PyErr {
    PyRuntimeError::new_err("Account current-view reader lock is poisoned")
}

#[pyfunction]
fn build_info() -> NativeBuildInfo {
    NativeBuildInfo {
        api_version: API_VERSION,
        owner: "Account".to_owned(),
        package_version: env!("CARGO_PKG_VERSION").to_owned(),
    }
}

#[pymodule]
fn _native_account_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "AccountInvalidCurrentViewError",
        module.py().get_type::<AccountInvalidCurrentViewError>(),
    )?;
    module.add(
        "AccountCurrentViewUnavailableError",
        module.py().get_type::<AccountCurrentViewUnavailableError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<AccountBalanceCurrent>()?;
    module.add_class::<AccountCollateralCurrent>()?;
    module.add_class::<AccountPositionCurrent>()?;
    module.add_class::<AccountEarnHoldingCurrent>()?;
    module.add_class::<AccountSegmentCurrent>()?;
    module.add_class::<AccountObservedOrderCurrent>()?;
    module.add_class::<AccountCurrentSnapshot>()?;
    module.add_class::<AccountCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    Ok(())
}
