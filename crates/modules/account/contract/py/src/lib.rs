use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use kairos_account_contract::{
    AccountChange as RustAccountChange, AccountCommandOutcome, AccountControlRpcClient,
    AccountEvent as RustAccountEvent, AccountHealthStatus, AccountIndexedView as RustView,
    AccountRefreshStatus, AccountSegmentsRequest, AdvanceAccountTimeRequest, ContractError,
    MarkToMarketRequest, SimulatedCapitalMutation, SimulatedCapitalMutationKind,
    SimulatedCapitalMutationQuery, SimulatedCapitalMutationStatus, SimulatedSettlement,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::{DecimalParts, Price, Quantity, SignedQuantity};
use kairos_primitives::execution::{FillId, OrderId, OrderSide};
use kairos_primitives::reference::{Currency, InstrumentId};
use kairos_primitives::runtime::{IdempotencyKey, InstanceIdentity};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};

create_exception!(
    _native_account_contract,
    AccountInvalidCurrentViewError,
    PyValueError
);
create_exception!(
    _native_account_contract,
    AccountControlUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_account_contract,
    AccountControlRejectedError,
    PyRuntimeError
);
create_exception!(
    _native_account_contract,
    AccountCurrentViewUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_account_contract,
    AccountInvalidEventError,
    PyValueError
);
create_exception!(
    _native_account_contract,
    AccountInvalidInputError,
    PyValueError
);

const API_VERSION: u32 = 1;

#[pyclass(name = "AccountClient", module = "kairospy._native_account_contract")]
struct NativeAccountClient {
    control_socket: PathBuf,
    view_root: Option<PathBuf>,
    account_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
    aeron_dir: Option<String>,
    channel: String,
    stream_id: i32,
    timeout: f64,
}

#[pymethods]
impl NativeAccountClient {
    #[new]
    #[pyo3(signature = (control_socket, *, account_id, workspace_id, view_root=None, launch_id=None, instance_id=None, aeron_dir=None, channel=None, stream_id=None, timeout=5.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        control_socket: PathBuf,
        account_id: String,
        workspace_id: String,
        view_root: Option<PathBuf>,
        launch_id: Option<String>,
        instance_id: Option<String>,
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        timeout: f64,
    ) -> PyResult<Self> {
        AccountId::new(account_id.clone())
            .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
        identity(workspace_id.clone(), launch_id.clone(), instance_id.clone())?;
        validate_client_facts("Account", stream_id, timeout)?;
        Ok(Self {
            control_socket,
            view_root,
            account_id,
            workspace_id,
            launch_id,
            instance_id,
            aeron_dir,
            channel: channel
                .unwrap_or_else(|| kairos_account_contract::DEFAULT_AERON_CHANNEL.to_owned()),
            stream_id: stream_id.unwrap_or(kairos_account_contract::ACCOUNT_EVENT_STREAM_ID),
            timeout,
        })
    }

    #[getter]
    fn control(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        construct_control(
            py,
            "kairospy._native_account_contract",
            "AccountControlClient",
            self.control_socket.clone(),
            self.timeout,
        )
    }

    #[getter]
    fn current(&self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let Some(root) = &self.view_root else {
            return Ok(None);
        };
        let module = PyModule::import(py, "kairospy._native_account_contract")?;
        Ok(Some(
            module
                .getattr("AccountCurrentView")?
                .call1((
                    root.clone(),
                    self.account_id.clone(),
                    self.workspace_id.clone(),
                    self.launch_id.clone(),
                    self.instance_id.clone(),
                ))?
                .unbind(),
        ))
    }

    #[getter]
    fn events(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        construct_events(
            py,
            "kairospy._native_account_contract",
            self.aeron_dir.clone(),
            &self.channel,
            self.stream_id,
        )
    }
}

fn validate_client_facts(owner: &str, stream_id: Option<i32>, timeout: f64) -> PyResult<()> {
    if !timeout.is_finite() || timeout <= 0.0 {
        return Err(AccountInvalidInputError::new_err(format!(
            "{owner} control timeout must be finite and positive"
        )));
    }
    if stream_id.is_some_and(|value| value <= 0) {
        return Err(AccountInvalidInputError::new_err(format!(
            "{owner} event stream_id must be positive"
        )));
    }
    Ok(())
}

fn construct_control(
    py: Python<'_>,
    module_name: &str,
    class_name: &str,
    socket: PathBuf,
    timeout: f64,
) -> PyResult<Py<PyAny>> {
    let module = PyModule::import(py, module_name)?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("timeout", timeout)?;
    Ok(module
        .getattr(class_name)?
        .call((socket,), Some(&kwargs))?
        .unbind())
}

fn construct_events(
    py: Python<'_>,
    owner_module_name: &str,
    aeron_dir: Option<String>,
    channel: &str,
    stream_id: i32,
) -> PyResult<Py<PyAny>> {
    let platform = PyModule::import(py, "kairospy.infrastructure.transport.native_event")?;
    let owner = PyModule::import(py, owner_module_name)?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("decoder", owner.getattr("decode_event")?)?;
    kwargs.set_item("aeron_dir", aeron_dir)?;
    kwargs.set_item("channel", channel)?;
    kwargs.set_item("stream_id", stream_id)?;
    Ok(platform
        .getattr("NativeEventSource")?
        .call((), Some(&kwargs))?
        .unbind())
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
struct NativeBuildInfo {
    #[pyo3(get)]
    api_version: u32,
    #[pyo3(get)]
    owner: String,
    #[pyo3(get)]
    package_version: String,
    #[pyo3(get)]
    contract_fingerprint: String,
}

#[pyclass(
    name = "AccountSegmentsRequest",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeAccountSegmentsRequest {
    inner: AccountSegmentsRequest,
}

#[pymethods]
impl NativeAccountSegmentsRequest {
    #[new]
    #[pyo3(signature = (segments=Vec::new()))]
    fn new(segments: Vec<String>) -> PyResult<Self> {
        Ok(Self {
            inner: AccountSegmentsRequest {
                segments: segments
                    .into_iter()
                    .map(|value| {
                        SegmentKey::new(value)
                            .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
                    })
                    .collect::<PyResult<_>>()?,
            },
        })
    }

    #[getter]
    fn segments(&self) -> Vec<String> {
        self.inner
            .segments
            .iter()
            .map(ToString::to_string)
            .collect()
    }
}

#[pyclass(
    name = "MarkToMarketRequest",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeMarkToMarketRequest {
    inner: MarkToMarketRequest,
}

#[pymethods]
impl NativeMarkToMarketRequest {
    #[new]
    fn new(
        segment_key: String,
        instrument_id: String,
        quote_asset: String,
        mark_price: String,
        observed_at_unix_nanos: u64,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: MarkToMarketRequest {
                segment_key: SegmentKey::new(segment_key)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                instrument_id: InstrumentId::new(instrument_id)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                quote_asset: Currency::new(quote_asset)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                mark_price: price(&mark_price)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
            },
        })
    }

    #[getter]
    fn segment_key(&self) -> String {
        self.inner.segment_key.to_string()
    }
}

#[pyclass(
    name = "AdvanceAccountTimeRequest",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeAdvanceAccountTimeRequest {
    inner: AdvanceAccountTimeRequest,
}

#[pymethods]
impl NativeAdvanceAccountTimeRequest {
    #[new]
    fn new(event_time_unix_nanos: u64) -> Self {
        Self {
            inner: AdvanceAccountTimeRequest {
                event_time_unix_nanos: UnixNanos::new(event_time_unix_nanos),
            },
        }
    }

    #[getter]
    fn event_time_unix_nanos(&self) -> u64 {
        self.inner.event_time_unix_nanos.get()
    }
}

#[pyclass(
    name = "SimulatedSettlement",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeSimulatedSettlement {
    inner: SimulatedSettlement,
}

#[pymethods]
impl NativeSimulatedSettlement {
    #[new]
    #[pyo3(signature = (fill_id, segment_key, instrument_id, quantity, price, side, occurred_at_unix_nanos, *, order_id=None, settlement_asset=None, settlement_delta=None, fee_asset=None, fee_amount=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        fill_id: String,
        segment_key: String,
        instrument_id: String,
        quantity: String,
        price: String,
        side: String,
        occurred_at_unix_nanos: u64,
        order_id: Option<String>,
        settlement_asset: Option<String>,
        settlement_delta: Option<String>,
        fee_asset: Option<String>,
        fee_amount: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: SimulatedSettlement {
                fill_id: FillId::new(fill_id)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                order_id: order_id
                    .map(OrderId::new)
                    .transpose()
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                segment_key: SegmentKey::new(segment_key)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                instrument_id: InstrumentId::new(instrument_id)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                quantity: quantity_value(&quantity)?,
                price: price_value(&price)?,
                side: side
                    .parse::<OrderSide>()
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                settlement_asset: optional_currency(settlement_asset)?,
                settlement_delta: settlement_delta
                    .map(|value| signed_quantity(&value))
                    .transpose()?,
                fee_asset: optional_currency(fee_asset)?,
                fee_amount: fee_amount
                    .map(|value| signed_quantity(&value))
                    .transpose()?,
                occurred_at_unix_nanos: UnixNanos::new(occurred_at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "SimulatedCapitalMutation",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeSimulatedCapitalMutation {
    inner: SimulatedCapitalMutation,
}

#[pymethods]
impl NativeSimulatedCapitalMutation {
    #[new]
    #[pyo3(signature = (mutation_id, segment_key, asset, amount, kind, occurred_at_unix_nanos, *, product_id=None))]
    fn new(
        mutation_id: String,
        segment_key: String,
        asset: String,
        amount: String,
        kind: String,
        occurred_at_unix_nanos: u64,
        product_id: Option<String>,
    ) -> PyResult<Self> {
        let kind = match kind.trim().to_ascii_lowercase().as_str() {
            "debit_liquid" => SimulatedCapitalMutationKind::DebitLiquid,
            "credit_liquid" => SimulatedCapitalMutationKind::CreditLiquid,
            "subscribe_earn" => SimulatedCapitalMutationKind::SubscribeEarn,
            "redeem_earn" => SimulatedCapitalMutationKind::RedeemEarn,
            _ => {
                return Err(AccountInvalidInputError::new_err(
                    "simulation capital mutation kind is invalid",
                ));
            },
        };
        Ok(Self {
            inner: SimulatedCapitalMutation {
                mutation_id: IdempotencyKey::new(mutation_id)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                segment_key: SegmentKey::new(segment_key)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                asset: Currency::new(asset)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                amount: quantity_value(&amount)?,
                kind,
                product_id: validated_optional_text(product_id, "product_id")?,
                occurred_at_unix_nanos: UnixNanos::new(occurred_at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "SimulatedCapitalMutationQuery",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeSimulatedCapitalMutationQuery {
    inner: SimulatedCapitalMutationQuery,
}

#[pymethods]
impl NativeSimulatedCapitalMutationQuery {
    #[new]
    fn new(mutation_id: String, segment_key: String) -> PyResult<Self> {
        Ok(Self {
            inner: SimulatedCapitalMutationQuery {
                mutation_id: IdempotencyKey::new(mutation_id)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                segment_key: SegmentKey::new(segment_key)
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
            },
        })
    }
}

#[pyclass(
    name = "SimulatedCapitalMutationStatusResponse",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct NativeSimulatedCapitalMutationStatusResponse {
    #[pyo3(get)]
    status: String,
}

#[pyclass(
    name = "AccountHealth",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct NativeAccountHealth {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    lease_valid: Option<bool>,
    #[pyo3(get)]
    generation: u64,
    #[pyo3(get)]
    event_sequence: u64,
}

#[pyclass(
    name = "AccountCommandStatus",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct NativeAccountCommandStatus {
    #[pyo3(get)]
    status: String,
}

#[pyclass(
    name = "AccountRefreshResponse",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct NativeAccountRefreshResponse {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    account_id: Option<String>,
    #[pyo3(get)]
    segments: Vec<String>,
}

#[pyclass(
    name = "AdvanceAccountTimeResponse",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct NativeAdvanceAccountTimeResponse {
    #[pyo3(get)]
    event_time_unix_nanos: u64,
}

#[pyclass(
    name = "AccountControlClient",
    module = "kairospy._native_account_contract"
)]
struct NativeAccountControlClient {
    client: kairos_protocol::ContractClient,
    timeout: Duration,
}

#[pymethods]
impl NativeAccountControlClient {
    #[getter]
    fn socket_path(&self) -> PathBuf {
        self.client.control_socket_path().to_path_buf()
    }

    #[new]
    #[pyo3(signature = (socket_path, *, timeout=5.0))]
    fn new(socket_path: PathBuf, timeout: f64) -> PyResult<Self> {
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(AccountInvalidInputError::new_err(
                "Account control timeout must be finite and positive",
            ));
        }
        Ok(Self {
            client: kairos_protocol::ContractClient::control_only(socket_path),
            timeout: Duration::from_secs_f64(timeout),
        })
    }

    fn health(&self, py: Python<'_>) -> PyResult<NativeAccountHealth> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py
            .detach(move || run_control(timeout, async move { client.control().health().await }))?;
        Ok(NativeAccountHealth {
            status: match value.status {
                AccountHealthStatus::Ready => "ready",
                AccountHealthStatus::Degraded => "degraded",
            }
            .to_owned(),
            lease_valid: value.lease_valid,
            generation: value.generation.get(),
            event_sequence: value.event_sequence.get(),
        })
    }

    fn refresh(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAccountSegmentsRequest>,
    ) -> PyResult<NativeAccountRefreshResponse> {
        self.refresh_like(py, request.inner.clone(), false)
    }

    fn reconcile(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAccountSegmentsRequest>,
    ) -> PyResult<NativeAccountRefreshResponse> {
        self.refresh_like(py, request.inner.clone(), true)
    }

    fn mark_to_market(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeMarkToMarketRequest>,
    ) -> PyResult<NativeAccountCommandStatus> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().mark_to_market(request).await
            })
        })?;
        Ok(NativeAccountCommandStatus {
            status: match value.status {
                AccountCommandOutcome::Applied => "applied",
            }
            .to_owned(),
        })
    }

    fn advance_time(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAdvanceAccountTimeRequest>,
    ) -> PyResult<NativeAdvanceAccountTimeResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner;
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().advance_time(request).await
            })
        })?;
        Ok(NativeAdvanceAccountTimeResponse {
            event_time_unix_nanos: value.event_time_unix_nanos.get(),
        })
    }

    fn apply_simulated_settlement(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeSimulatedSettlement>,
    ) -> PyResult<NativeAccountCommandStatus> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client.control().apply_simulated_settlement(request).await
            })
        })?;
        Ok(project_command_status(value))
    }

    fn apply_simulated_capital_mutation(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeSimulatedCapitalMutation>,
    ) -> PyResult<NativeAccountCommandStatus> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client
                    .control()
                    .apply_simulated_capital_mutation(request)
                    .await
            })
        })?;
        Ok(project_command_status(value))
    }

    fn query_simulated_capital_mutation(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeSimulatedCapitalMutationQuery>,
    ) -> PyResult<NativeSimulatedCapitalMutationStatusResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let request = request.inner.clone();
        let value = py.detach(move || {
            run_control(timeout, async move {
                client
                    .control()
                    .query_simulated_capital_mutation(request)
                    .await
            })
        })?;
        Ok(NativeSimulatedCapitalMutationStatusResponse {
            status: match value.status {
                SimulatedCapitalMutationStatus::Applied => "applied",
                SimulatedCapitalMutationStatus::NotFound => "not_found",
            }
            .to_owned(),
        })
    }
}

impl NativeAccountControlClient {
    fn refresh_like(
        &self,
        py: Python<'_>,
        request: AccountSegmentsRequest,
        reconcile: bool,
    ) -> PyResult<NativeAccountRefreshResponse> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let value = py.detach(move || {
            run_control(timeout, async move {
                if reconcile {
                    client.control().reconcile(request).await
                } else {
                    client.control().refresh(request).await
                }
            })
        })?;
        Ok(NativeAccountRefreshResponse {
            status: match value.status {
                AccountRefreshStatus::Completed => "completed",
            }
            .to_owned(),
            account_id: value.account_id.map(|value| value.to_string()),
            segments: value
                .segments
                .into_iter()
                .map(|value| value.to_string())
                .collect(),
        })
    }
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
}

#[pymethods]
impl NativeDecimal {
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let decimal = PyModule::import(py, "decimal")?.getattr("Decimal")?;
        let sign = if self.mantissa < 0 { "-" } else { "" };
        let mut digits = self.mantissa.unsigned_abs().to_string();
        let text = if self.scale == 0 {
            format!("{sign}{digits}")
        } else {
            let scale = usize::from(self.scale);
            if digits.len() <= scale {
                digits.insert_str(0, &"0".repeat(scale + 1 - digits.len()));
            }
            let split = digits.len() - scale;
            format!("{sign}{}.{}", &digits[..split], &digits[split..])
        };
        Ok(decimal.call1((text,))?.unbind())
    }
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountEventMetadata {
    #[pyo3(get)]
    event_id: String,
    #[pyo3(get)]
    stream_id: String,
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    producer: String,
    #[pyo3(get)]
    workspace_id: String,
    #[pyo3(get)]
    launch_id: Option<String>,
    #[pyo3(get)]
    instance_id: Option<String>,
    #[pyo3(get)]
    correlation_id: Option<String>,
    #[pyo3(get)]
    causation_id: Option<String>,
    #[pyo3(get)]
    occurred_at_unix_nanos: u64,
    #[pyo3(get)]
    published_at_unix_nanos: u64,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountEventProvenance {
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    provider_event_id: Option<String>,
    #[pyo3(get)]
    provider_sequence: Option<u64>,
    #[pyo3(get)]
    provider_occurred_at_unix_nanos: Option<u64>,
    #[pyo3(get)]
    provider_received_at_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountBalanceEvent {
    #[pyo3(get)]
    asset_id: String,
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
struct AccountPositionEvent {
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    position_side: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    average_price: Option<NativeDecimal>,
    #[pyo3(get)]
    mark_price: Option<NativeDecimal>,
    #[pyo3(get)]
    unrealized_pnl: Option<NativeDecimal>,
    #[pyo3(get)]
    realized_pnl: Option<NativeDecimal>,
    #[pyo3(get)]
    observed_at_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountPositionRemovedEvent {
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    position_side: String,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountEarnHoldingEvent {
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
struct AccountValuationEvent {
    #[pyo3(get)]
    valuation_asset_id: Option<String>,
    #[pyo3(get)]
    equity: Option<NativeDecimal>,
    #[pyo3(get)]
    initial_equity: Option<NativeDecimal>,
    #[pyo3(get)]
    net_profit: Option<NativeDecimal>,
    #[pyo3(get)]
    observed_at_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountStatusEvent {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    freshness: String,
    #[pyo3(get)]
    reason: Option<String>,
    #[pyo3(get)]
    trading_enabled: bool,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountObservedOrderEvent {
    #[pyo3(get)]
    observation_id: String,
    #[pyo3(get)]
    source_id: String,
    #[pyo3(get)]
    execution_order_id: Option<String>,
    #[pyo3(get)]
    remote_order_id: Option<String>,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    market_id: String,
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    quantity: NativeDecimal,
    #[pyo3(get)]
    filled_quantity: NativeDecimal,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    observed_at_unix_nanos: Option<u64>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountObservedOrderRemovedEvent {
    #[pyo3(get)]
    observation_id: String,
    #[pyo3(get)]
    execution_order_id: Option<String>,
    #[pyo3(get)]
    remote_order_id: Option<String>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
#[derive(Clone)]
struct AccountEventChange {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    asset_id: Option<String>,
    #[pyo3(get)]
    holding_key: Option<String>,
    #[pyo3(get)]
    balance: Option<AccountBalanceEvent>,
    #[pyo3(get)]
    position: Option<AccountPositionEvent>,
    #[pyo3(get)]
    removed_position: Option<AccountPositionRemovedEvent>,
    #[pyo3(get)]
    earn_holding: Option<AccountEarnHoldingEvent>,
    #[pyo3(get)]
    valuation: Option<AccountValuationEvent>,
    #[pyo3(get)]
    status: Option<AccountStatusEvent>,
    #[pyo3(get)]
    observed_order: Option<AccountObservedOrderEvent>,
    #[pyo3(get)]
    removed_observed_order: Option<AccountObservedOrderRemovedEvent>,
}

#[pyclass(frozen, module = "kairospy._native_account_contract")]
struct AccountEvent {
    #[pyo3(get)]
    metadata: AccountEventMetadata,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    change: AccountEventChange,
    #[pyo3(get)]
    provenance: Option<AccountEventProvenance>,
}

#[pymethods]
impl AccountEvent {
    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, *, status=None, freshness=None, trading_enabled=true, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_status(
        account_id: String,
        segment_key: String,
        sequence: u64,
        status: Option<String>,
        freshness: Option<String>,
        trading_enabled: bool,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        simulation_event(
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            AccountEventChange {
                kind: "status_changed".to_owned(),
                segment_key: String::new(),
                asset_id: None,
                holding_key: None,
                balance: None,
                position: None,
                removed_position: None,
                earn_holding: None,
                valuation: None,
                status: Some(AccountStatusEvent {
                    status: status.unwrap_or_else(|| "active".to_owned()),
                    freshness: freshness.unwrap_or_else(|| "fresh".to_owned()),
                    reason: None,
                    trading_enabled,
                }),
                observed_order: None,
                removed_observed_order: None,
            },
        )
    }

    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, asset, total, available, *, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_balance(
        account_id: String,
        segment_key: String,
        sequence: u64,
        asset: String,
        total: String,
        available: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        simulation_event(
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            AccountEventChange {
                kind: "balance_changed".to_owned(),
                segment_key: String::new(),
                asset_id: None,
                holding_key: None,
                balance: Some(AccountBalanceEvent {
                    asset_id: asset.clone(),
                    asset,
                    total: native_decimal(&total)?,
                    available: Some(native_decimal(&available)?),
                    locked: None,
                    borrowed: None,
                    interest: None,
                }),
                position: None,
                removed_position: None,
                earn_holding: None,
                valuation: None,
                status: None,
                observed_order: None,
                removed_observed_order: None,
            },
        )
    }

    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, equity, *, launch_id=None, instance_id=None))]
    fn simulation_valuation(
        account_id: String,
        segment_key: String,
        sequence: u64,
        equity: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        simulation_event(
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            AccountEventChange {
                kind: "equity_changed".to_owned(),
                segment_key: String::new(),
                asset_id: None,
                holding_key: None,
                balance: None,
                position: None,
                removed_position: None,
                earn_holding: None,
                valuation: Some(AccountValuationEvent {
                    valuation_asset_id: None,
                    equity: Some(native_decimal(&equity)?),
                    initial_equity: None,
                    net_profit: None,
                    observed_at_unix_nanos: None,
                }),
                status: None,
                observed_order: None,
                removed_observed_order: None,
            },
        )
    }

    #[getter]
    fn stream_id(&self) -> &str {
        &self.metadata.stream_id
    }
    #[getter]
    fn sequence(&self) -> u64 {
        self.metadata.sequence
    }
    #[getter]
    fn producer(&self) -> &str {
        &self.metadata.producer
    }
    #[getter]
    fn occurred_at_unix_nanos(&self) -> u64 {
        self.metadata.occurred_at_unix_nanos
    }
    #[getter]
    fn launch_id(&self) -> Option<&str> {
        self.metadata.launch_id.as_deref()
    }
    #[getter]
    fn instance_id(&self) -> Option<&str> {
        self.metadata.instance_id.as_deref()
    }
    #[getter]
    fn changes(&self) -> Vec<AccountEventChange> {
        vec![self.change.clone()]
    }
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
    root: PathBuf,
    identity: InstanceIdentity,
    account_id: AccountId,
    path: PathBuf,
    reader: Mutex<Option<RustView>>,
    closed: Mutex<bool>,
}

#[pymethods]
impl AccountCurrentView {
    #[new]
    #[pyo3(signature = (root, account_id, workspace_id, launch_id=None, instance_id=None))]
    fn new(
        _py: Python<'_>,
        root: PathBuf,
        account_id: String,
        workspace_id: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
    ) -> PyResult<Self> {
        let identity = identity(workspace_id, launch_id, instance_id)?;
        let account_id = AccountId::new(account_id)
            .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
        let path = kairos_account_contract::account_indexed_environment_path(
            &root,
            &identity,
            &account_id,
        )
        .map_err(contract_error)?;
        Ok(Self {
            creator_pid: std::process::id(),
            root,
            identity,
            account_id,
            path,
            reader: Mutex::new(None),
            closed: Mutex::new(false),
        })
    }

    #[getter]
    fn path(&self) -> PathBuf {
        self.path.clone()
    }

    fn snapshot(&self, py: Python<'_>) -> PyResult<AccountCurrentSnapshot> {
        self.read(py, |reader| {
            let snapshot = reader.snapshot().map_err(contract_error)?;
            project_snapshot(snapshot).map_err(contract_error)
        })
    }

    fn close(&self) -> PyResult<()> {
        self.ensure_process()?;
        *self.closed.lock().map_err(|_| lock_error())? = true;
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
    fn read<T: Send>(
        &self,
        py: Python<'_>,
        operation: impl FnOnce(&RustView) -> PyResult<T> + Send,
    ) -> PyResult<T> {
        self.ensure_process()?;
        py.detach(|| {
            if *self.closed.lock().map_err(|_| lock_error())? {
                return Err(PyRuntimeError::new_err(
                    "Account current-view reader is closed",
                ));
            }
            let mut reader = self.reader.lock().map_err(|_| lock_error())?;
            if reader.is_none() {
                *reader = Some(
                    RustView::open(self.root.clone(), &self.identity, self.account_id.clone())
                        .map_err(contract_error)?,
                );
            }
            operation(reader.as_ref().expect("reader initialized above"))
        })
    }

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

fn project_event(value: RustAccountEvent) -> AccountEvent {
    let metadata = AccountEventMetadata {
        event_id: value.metadata.event_id.to_string(),
        stream_id: value.metadata.stream_id,
        sequence: value.metadata.sequence.get(),
        producer: value.metadata.producer_id.to_string(),
        workspace_id: value.metadata.workspace_id.to_string(),
        launch_id: value.metadata.launch_id.map(|item| item.to_string()),
        instance_id: value.metadata.instance_id.map(|item| item.to_string()),
        correlation_id: value.metadata.correlation_id,
        causation_id: value.metadata.causation_id,
        occurred_at_unix_nanos: value.metadata.occurred_at_unix_nanos.get(),
        published_at_unix_nanos: value.metadata.published_at_unix_nanos.get(),
    };
    let provenance = value.provenance.map(|item| AccountEventProvenance {
        source_id: item.source_id,
        provider_event_id: item.provider_event_id,
        provider_sequence: item.provider_sequence,
        provider_occurred_at_unix_nanos: item
            .provider_occurred_at_unix_nanos
            .map(|value| value.get()),
        provider_received_at_unix_nanos: item
            .provider_received_at_unix_nanos
            .map(|value| value.get()),
    });
    let mut change = AccountEventChange {
        kind: value.change.kind().to_owned(),
        segment_key: value.segment_key.to_string(),
        asset_id: None,
        holding_key: None,
        balance: None,
        position: None,
        removed_position: None,
        earn_holding: None,
        valuation: None,
        status: None,
        observed_order: None,
        removed_observed_order: None,
    };
    match value.change {
        RustAccountChange::BalanceUpserted(item) => {
            change.balance = Some(AccountBalanceEvent {
                asset_id: item.asset_id.to_string(),
                asset: item.asset_code.unwrap_or_else(|| item.asset_id.to_string()),
                total: decimal_parts(item.total),
                available: item.available.map(decimal_parts),
                locked: item.locked.map(decimal_parts),
                borrowed: item.borrowed.map(decimal_parts),
                interest: item.interest.map(decimal_parts),
            });
        },
        RustAccountChange::BalanceRemoved { asset_id } => {
            change.asset_id = Some(asset_id.to_string());
        },
        RustAccountChange::PositionUpserted(item) => {
            change.position = Some(AccountPositionEvent {
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                position_side: item.position_side.as_str().to_owned(),
                quantity: decimal_parts(item.quantity),
                average_price: item.average_price.map(decimal_parts),
                mark_price: item.mark_price.map(decimal_parts),
                unrealized_pnl: item.unrealized_pnl.map(decimal_parts),
                realized_pnl: item.realized_pnl.map(decimal_parts),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            });
        },
        RustAccountChange::PositionRemoved(item) => {
            change.removed_position = Some(AccountPositionRemovedEvent {
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                position_side: item.position_side.as_str().to_owned(),
            });
        },
        RustAccountChange::EarnHoldingUpserted(item) => {
            change.earn_holding = Some(AccountEarnHoldingEvent {
                holding_key: item.holding_key,
                participant_position_id: item.participant_position_id,
                product_id: item.product_id,
                asset: item.asset,
                principal: decimal_parts(item.principal),
                redeemable: item.redeemable.map(decimal_parts),
                state: item.state.as_str().to_owned(),
                participant_state: item.participant_state,
                liquidity: item.liquidity.as_str().to_owned(),
                notice_seconds: item.notice_seconds,
                matures_at_unix_nanos: item.matures_at_unix_nanos.map(|value| value.get()),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            });
        },
        RustAccountChange::EarnHoldingRemoved { holding_key } => {
            change.holding_key = Some(holding_key);
        },
        RustAccountChange::ValuationChanged(item) => {
            change.valuation = Some(AccountValuationEvent {
                valuation_asset_id: item.valuation_asset_id.map(|value| value.to_string()),
                equity: item.equity.map(decimal_parts),
                initial_equity: item.initial_equity.map(decimal_parts),
                net_profit: item.net_profit.map(decimal_parts),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            });
        },
        RustAccountChange::StatusChanged(item) => {
            change.status = Some(AccountStatusEvent {
                status: item.status.as_str().to_owned(),
                freshness: item.freshness.as_str().to_owned(),
                reason: item.reason,
                trading_enabled: matches!(
                    item.status,
                    kairos_account_contract::AccountStatus::Active
                ),
            });
        },
        RustAccountChange::ObservedOrderUpserted(item) => {
            change.observed_order = Some(AccountObservedOrderEvent {
                observation_id: item.observation_id,
                source_id: item.source_id,
                execution_order_id: item.execution_order_id.map(|value| value.to_string()),
                remote_order_id: item.remote_order_id.map(|value| value.to_string()),
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                side: match item.side {
                    kairos_primitives::execution::OrderSide::Buy => "buy",
                    kairos_primitives::execution::OrderSide::Sell => "sell",
                }
                .to_owned(),
                quantity: decimal_parts(item.quantity),
                filled_quantity: decimal_parts(item.filled_quantity),
                status: item.status.as_str().to_owned(),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            });
        },
        RustAccountChange::ObservedOrderRemoved(item) => {
            change.removed_observed_order = Some(AccountObservedOrderRemovedEvent {
                observation_id: item.observation_id,
                execution_order_id: item.execution_order_id.map(|value| value.to_string()),
                remote_order_id: item.remote_order_id.map(|value| value.to_string()),
            });
        },
    }
    AccountEvent {
        metadata,
        account_id: value.account_id.to_string(),
        change,
        provenance,
    }
}

fn decimal(value: &Decimal64) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
    }
}
fn decimal_parts(value: kairos_primitives::decimal::DecimalParts) -> NativeDecimal {
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
            .map_err(|error| AccountInvalidInputError::new_err(error.to_string())),
        (None, None) => InstanceIdentity::unscoped(workspace_id)
            .map_err(|error| AccountInvalidInputError::new_err(error.to_string())),
        _ => Err(AccountInvalidInputError::new_err(
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

fn run_control<F, T, E>(timeout: Duration, future: F) -> PyResult<T>
where
    F: std::future::Future<Output = Result<T, E>>,
    E: kairos_protocol::contract::ControlCallError,
{
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .map_err(|error| AccountControlUnavailableError::new_err(error.to_string()))?;
    runtime
        .block_on(async move { tokio::time::timeout(timeout, future).await })
        .map_err(|_| AccountControlUnavailableError::new_err("Account control request timed out"))?
        .map_err(|error| {
            if kairos_protocol::contract::is_control_rejection(&error) {
                AccountControlRejectedError::new_err(error.to_string())
            } else {
                AccountControlUnavailableError::new_err(error.to_string())
            }
        })
}

fn price(value: &str) -> PyResult<Price> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            })?;
    Price::new(parts.mantissa(), parts.scale())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn price_value(value: &str) -> PyResult<Price> {
    price(value)
}

fn quantity_value(value: &str) -> PyResult<Quantity> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            })?;
    Quantity::new(parts.mantissa(), parts.scale())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn signed_quantity(value: &str) -> PyResult<SignedQuantity> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            })?;
    SignedQuantity::new(parts.mantissa(), parts.scale())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn optional_currency(value: Option<String>) -> PyResult<Option<Currency>> {
    value
        .map(Currency::new)
        .transpose()
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn validated_optional_text(value: Option<String>, name: &str) -> PyResult<Option<String>> {
    value
        .map(|value| {
            let value = value.trim();
            if value.is_empty() {
                Err(AccountInvalidInputError::new_err(format!(
                    "{name} must be non-empty"
                )))
            } else {
                Ok(value.to_owned())
            }
        })
        .transpose()
}

fn project_command_status(
    value: kairos_account_contract::AccountCommandStatus,
) -> NativeAccountCommandStatus {
    NativeAccountCommandStatus {
        status: match value.status {
            AccountCommandOutcome::Applied => "applied",
        }
        .to_owned(),
    }
}

fn simulation_event(
    account_id: String,
    segment_key: String,
    sequence: u64,
    launch_id: Option<String>,
    instance_id: Option<String>,
    mut change: AccountEventChange,
) -> PyResult<AccountEvent> {
    AccountId::new(account_id.clone())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
    SegmentKey::new(segment_key.clone())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
    if launch_id.is_some() != instance_id.is_some() {
        return Err(AccountInvalidInputError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        ));
    }
    change.segment_key = segment_key;
    Ok(AccountEvent {
        metadata: AccountEventMetadata {
            event_id: format!("account:{account_id}:{sequence}"),
            stream_id: format!("account.events/account:{account_id}"),
            sequence,
            producer: "account".to_owned(),
            workspace_id: "simulation".to_owned(),
            launch_id,
            instance_id,
            correlation_id: None,
            causation_id: None,
            occurred_at_unix_nanos: sequence,
            published_at_unix_nanos: sequence,
        },
        account_id,
        change,
        provenance: None,
    })
}

fn native_decimal(value: &str) -> PyResult<NativeDecimal> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            })?;
    Ok(NativeDecimal {
        mantissa: parts.mantissa(),
        scale: parts.scale(),
    })
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
        contract_fingerprint: kairos_account_contract::CONTRACT_FINGERPRINT.to_owned(),
    }
}

#[pyfunction]
fn decode_event(payload: &[u8]) -> PyResult<AccountEvent> {
    kairos_account_contract::decode_event(payload)
        .map(project_event)
        .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))
}

#[pyfunction]
#[pyo3(signature = (root, account_id, workspace_id, launch_id=None, instance_id=None))]
fn indexed_environment_path(
    root: PathBuf,
    account_id: String,
    workspace_id: String,
    launch_id: Option<String>,
    instance_id: Option<String>,
) -> PyResult<PathBuf> {
    let identity = identity(workspace_id, launch_id, instance_id)?;
    let account_id = AccountId::new(account_id)
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
    kairos_account_contract::account_indexed_environment_path(root, &identity, &account_id)
        .map_err(contract_error)
}

#[pymodule]
fn _native_account_contract(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module
        .py()
        .get_type::<AccountInvalidInputError>()
        .setattr("code", "invalid_input")?;
    module
        .py()
        .get_type::<AccountInvalidCurrentViewError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<AccountCurrentViewUnavailableError>()
        .setattr("code", "current_view_unavailable")?;
    module
        .py()
        .get_type::<AccountInvalidEventError>()
        .setattr("code", "invalid_wire_data")?;
    module
        .py()
        .get_type::<AccountControlUnavailableError>()
        .setattr("code", "transport_unavailable")?;
    module
        .py()
        .get_type::<AccountControlRejectedError>()
        .setattr("code", "operation_rejected")?;
    module.add(
        "ACCOUNT_EVENT_STREAM_ID",
        kairos_account_contract::ACCOUNT_EVENT_STREAM_ID,
    )?;
    module.add(
        "DEFAULT_AERON_CHANNEL",
        kairos_account_contract::DEFAULT_AERON_CHANNEL,
    )?;
    module.add(
        "AccountInvalidInputError",
        module.py().get_type::<AccountInvalidInputError>(),
    )?;
    module.add(
        "AccountInvalidCurrentViewError",
        module.py().get_type::<AccountInvalidCurrentViewError>(),
    )?;
    module.add(
        "AccountCurrentViewUnavailableError",
        module.py().get_type::<AccountCurrentViewUnavailableError>(),
    )?;
    module.add(
        "AccountInvalidEventError",
        module.py().get_type::<AccountInvalidEventError>(),
    )?;
    module.add(
        "AccountControlUnavailableError",
        module.py().get_type::<AccountControlUnavailableError>(),
    )?;
    module.add(
        "AccountControlRejectedError",
        module.py().get_type::<AccountControlRejectedError>(),
    )?;
    module.add_class::<NativeBuildInfo>()?;
    module.add_class::<NativeAccountClient>()?;
    module.add_class::<NativeAccountSegmentsRequest>()?;
    module.add_class::<NativeMarkToMarketRequest>()?;
    module.add_class::<NativeAdvanceAccountTimeRequest>()?;
    module.add_class::<NativeSimulatedSettlement>()?;
    module.add_class::<NativeSimulatedCapitalMutation>()?;
    module.add_class::<NativeSimulatedCapitalMutationQuery>()?;
    module.add_class::<NativeSimulatedCapitalMutationStatusResponse>()?;
    module.add_class::<NativeAccountHealth>()?;
    module.add_class::<NativeAccountCommandStatus>()?;
    module.add_class::<NativeAccountRefreshResponse>()?;
    module.add_class::<NativeAdvanceAccountTimeResponse>()?;
    module.add_class::<NativeAccountControlClient>()?;
    module.add_class::<NativeDecimal>()?;
    module.add_class::<AccountEventMetadata>()?;
    module.add_class::<AccountEventProvenance>()?;
    module.add_class::<AccountBalanceEvent>()?;
    module.add_class::<AccountPositionEvent>()?;
    module.add_class::<AccountPositionRemovedEvent>()?;
    module.add_class::<AccountEarnHoldingEvent>()?;
    module.add_class::<AccountValuationEvent>()?;
    module.add_class::<AccountStatusEvent>()?;
    module.add_class::<AccountObservedOrderEvent>()?;
    module.add_class::<AccountObservedOrderRemovedEvent>()?;
    module.add_class::<AccountEventChange>()?;
    module.add_class::<AccountEvent>()?;
    module.add_class::<AccountBalanceCurrent>()?;
    module.add_class::<AccountCollateralCurrent>()?;
    module.add_class::<AccountPositionCurrent>()?;
    module.add_class::<AccountEarnHoldingCurrent>()?;
    module.add_class::<AccountSegmentCurrent>()?;
    module.add_class::<AccountObservedOrderCurrent>()?;
    module.add_class::<AccountCurrentSnapshot>()?;
    module.add_class::<AccountCurrentView>()?;
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    module.add_function(wrap_pyfunction!(decode_event, module)?)?;
    module.add_function(wrap_pyfunction!(indexed_environment_path, module)?)?;
    Ok(())
}
