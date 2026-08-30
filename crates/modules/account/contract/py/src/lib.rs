use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use _native_transport::direct::DirectAeronSubscription;
use _native_transport::lease::{EventLease, EventLeaseError};
use kairos_account_contract::{
    AccountChange as RustAccountChange, AccountCommandOutcome, AccountControlRpcClient,
    AccountEvent as RustAccountEvent, AccountEventView as RustAccountEventView,
    AccountHealthStatus, AccountIndexedView as RustView, AccountRefreshStatus,
    AccountSegmentsRequest, AdvanceAccountTimeRequest, ContractError, MarkToMarketRequest,
    SimulatedCapitalMutation, SimulatedCapitalMutationKind, SimulatedCapitalMutationQuery,
    SimulatedCapitalMutationStatus, SimulatedSettlement,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::capital::EarnProductId;
use kairos_primitives::decimal::{DecimalParts, Money, Price, Quantity, SignedQuantity};
use kairos_primitives::execution::{FillId, OrderId, OrderSide};
use kairos_primitives::reference::{Currency, InstrumentId};
use kairos_primitives::runtime::{IdempotencyKey, InstanceIdentity};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;
use kairos_protocol::{EventMetadataOwned, decode_event_metadata};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use pyo3::{PyClass, create_exception};

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
        Ok(Py::new(
            py,
            AccountLiveSubscription::new(
                self.aeron_dir.clone(),
                Some(self.channel.clone()),
                Some(self.stream_id),
                _native_transport::DEFAULT_MAX_PAYLOAD_LEN,
            )?,
        )?
        .into_any())
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
        mark_price: &Bound<'_, PyAny>,
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
                mark_price: price_input(mark_price)?,
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
        quantity: &Bound<'_, PyAny>,
        price: &Bound<'_, PyAny>,
        side: String,
        occurred_at_unix_nanos: u64,
        order_id: Option<String>,
        settlement_asset: Option<String>,
        settlement_delta: Option<Bound<'_, PyAny>>,
        fee_asset: Option<String>,
        fee_amount: Option<Bound<'_, PyAny>>,
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
                quantity: quantity_input(quantity)?,
                price: price_input(price)?,
                side: side
                    .parse::<OrderSide>()
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
                settlement_asset: optional_currency(settlement_asset)?,
                settlement_delta: settlement_delta
                    .as_ref()
                    .map(signed_quantity_input)
                    .transpose()?,
                fee_asset: optional_currency(fee_asset)?,
                fee_amount: fee_amount.as_ref().map(signed_quantity_input).transpose()?,
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
        amount: &Bound<'_, PyAny>,
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
                amount: quantity_input(amount)?,
                kind,
                product_id: validated_optional_text(product_id, "product_id")?
                    .map(EarnProductId::new)
                    .transpose()
                    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?,
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

#[pyclass(
    name = "_NativeSemanticDecimal",
    frozen,
    module = "kairospy._native_account_contract"
)]
#[derive(Clone)]
struct NativeDecimal {
    #[pyo3(get)]
    mantissa: i64,
    #[pyo3(get)]
    scale: u8,
    semantic_type: &'static str,
}

#[pymethods]
impl NativeDecimal {
    fn __str__(&self) -> String {
        native_decimal_text(self.mantissa, self.scale)
    }

    #[getter]
    fn semantic_type(&self) -> &'static str {
        self.semantic_type
    }

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

fn native_decimal_text(mantissa: i64, scale: u8) -> String {
    if scale == 0 {
        return mantissa.to_string();
    }
    let sign = if mantissa < 0 { "-" } else { "" };
    let scale = usize::from(scale);
    let mut digits = mantissa.unsigned_abs().to_string();
    if digits.len() <= scale {
        digits.insert_str(0, &"0".repeat(scale + 1 - digits.len()));
    }
    let split = digits.len() - scale;
    format!("{sign}{}.{}", &digits[..split], &digits[split..])
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
    producer_incarnation: u64,
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
struct AccountBalanceRemovedEvent {
    #[pyo3(get)]
    asset_id: String,
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
struct AccountEarnHoldingRemovedEvent {
    #[pyo3(get)]
    holding_key: String,
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
struct AccountEvent {
    #[pyo3(get)]
    metadata: AccountEventMetadata,
    #[pyo3(get)]
    kind: String,
    data: Py<PyAny>,
    #[pyo3(get)]
    account_id: String,
    #[pyo3(get)]
    segment_key: String,
    #[pyo3(get)]
    provenance: Option<AccountEventProvenance>,
}

#[pyclass(
    name = "AccountLiveEventView",
    frozen,
    module = "kairospy._native_account_contract"
)]
struct AccountLiveEventView {
    lease: Arc<EventLease>,
}

#[pymethods]
impl AccountLiveEventView {
    #[getter]
    fn kind(&self) -> PyResult<String> {
        self.with_view(|event| event.kind().as_str().to_owned())
    }

    #[getter]
    fn metadata(&self) -> PyResult<AccountEventMetadata> {
        self.with_view(|event| account_event_metadata(event.metadata()))?
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.with_owned(|event| project_event(py, event).map(|value| value.data))?
    }

    #[getter]
    fn account_id(&self) -> PyResult<String> {
        self.with_owned(|event| event.account_id.to_string())
    }

    #[getter]
    fn segment_key(&self) -> PyResult<String> {
        self.with_owned(|event| event.segment_key.to_string())
    }

    #[getter]
    fn provenance(&self, py: Python<'_>) -> PyResult<Option<AccountEventProvenance>> {
        self.with_owned(|event| project_event(py, event).map(|value| value.provenance))?
    }
}

impl AccountLiveEventView {
    fn with_view<R>(
        &self,
        read: impl for<'frame> FnOnce(RustAccountEventView<'frame>) -> R,
    ) -> PyResult<R> {
        self.lease
            .with_frame(|frame| {
                kairos_account_contract::decode_event_view(frame)
                    .map(read)
                    .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))
            })
            .map_err(account_lease_error)?
    }

    fn with_owned<R>(&self, read: impl FnOnce(RustAccountEvent) -> R) -> PyResult<R> {
        self.lease
            .with_frame(|frame| {
                kairos_account_contract::decode_event(frame)
                    .map(read)
                    .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))
            })
            .map_err(account_lease_error)?
    }
}

#[pyclass(
    name = "AccountLiveSubscription",
    module = "kairospy._native_account_contract",
    unsendable
)]
struct AccountLiveSubscription {
    inner: DirectAeronSubscription,
}

#[pymethods]
impl AccountLiveSubscription {
    #[new]
    #[pyo3(signature = (*, aeron_dir=None, channel=None, stream_id=None, max_payload_len=_native_transport::DEFAULT_MAX_PAYLOAD_LEN))]
    fn new(
        aeron_dir: Option<String>,
        channel: Option<String>,
        stream_id: Option<i32>,
        max_payload_len: usize,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: DirectAeronSubscription::connect(
                aeron_dir.as_deref(),
                channel
                    .as_deref()
                    .unwrap_or(kairos_account_contract::DEFAULT_AERON_CHANNEL),
                stream_id.unwrap_or(kairos_account_contract::ACCOUNT_EVENT_STREAM_ID),
                max_payload_len,
            )?,
        })
    }

    #[pyo3(signature = (visitor, *, fragment_limit=64))]
    fn poll_visit(
        &self,
        py: Python<'_>,
        visitor: Py<PyAny>,
        fragment_limit: i32,
    ) -> PyResult<usize> {
        self.inner.poll_visit(py, fragment_limit, |py, lease| {
            lease
                .with_frame(|frame| kairos_account_contract::decode_event_view(frame).map(|_| ()))
                .map_err(account_lease_error)?
                .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))?;
            visitor.call1(py, (Py::new(py, AccountLiveEventView { lease })?,))?;
            Ok(())
        })
    }

    fn close(&self) -> PyResult<()> {
        self.inner.close()
    }
}

fn account_lease_error(error: EventLeaseError) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

#[pymethods]
impl AccountEvent {
    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, *, status=None, freshness=None, trading_enabled=true, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_status(
        py: Python<'_>,
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
            py,
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            1,
            "account_status_changed",
            AccountStatusEvent {
                status: status.unwrap_or_else(|| "active".to_owned()),
                freshness: freshness.unwrap_or_else(|| "fresh".to_owned()),
                reason: None,
                trading_enabled,
            },
        )
    }

    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, asset, total, available, *, launch_id=None, instance_id=None))]
    #[allow(clippy::too_many_arguments)]
    fn simulation_balance(
        py: Python<'_>,
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
            py,
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            1,
            "balance_upserted",
            AccountBalanceEvent {
                asset_id: asset.clone(),
                asset,
                total: native_decimal(&total, "quantity")?,
                available: Some(native_decimal(&available, "quantity")?),
                locked: None,
                borrowed: None,
                interest: None,
            },
        )
    }

    #[staticmethod]
    #[pyo3(signature = (account_id, segment_key, sequence, equity, *, launch_id=None, instance_id=None, producer_incarnation=1))]
    fn simulation_valuation(
        py: Python<'_>,
        account_id: String,
        segment_key: String,
        sequence: u64,
        equity: String,
        launch_id: Option<String>,
        instance_id: Option<String>,
        producer_incarnation: u64,
    ) -> PyResult<Self> {
        simulation_event(
            py,
            account_id,
            segment_key,
            sequence,
            launch_id,
            instance_id,
            producer_incarnation,
            "valuation_changed",
            AccountValuationEvent {
                valuation_asset_id: None,
                equity: Some(native_decimal(&equity, "money")?),
                initial_equity: None,
                net_profit: None,
                observed_at_unix_nanos: None,
            },
        )
    }

    #[getter]
    fn data(&self, py: Python<'_>) -> Py<PyAny> {
        self.data.clone_ref(py)
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
    fn producer_incarnation(&self) -> u64 {
        self.metadata.producer_incarnation
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
            project_snapshot_direct(reader).map_err(contract_error)
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

enum MappedAccountRow {
    Segment(AccountSegmentCurrent),
    Balance(String, AccountBalanceCurrent),
    Collateral(AccountCollateralCurrent),
    Position(String, AccountPositionCurrent),
    EarnHolding(String, AccountEarnHoldingCurrent),
    Valuation(String, Option<NativeDecimal>),
    ObservedOrder(AccountObservedOrderCurrent),
}

fn project_snapshot_direct(reader: &RustView) -> Result<AccountCurrentSnapshot, ContractError> {
    use kairos_account_contract::{
        ACCOUNT_BALANCES_DATABASE, ACCOUNT_COLLATERAL_DATABASE, ACCOUNT_EARN_HOLDINGS_DATABASE,
        ACCOUNT_OBSERVED_ORDERS_DATABASE, ACCOUNT_POSITIONS_DATABASE, ACCOUNT_SEGMENTS_DATABASE,
        ACCOUNT_VALUATIONS_DATABASE,
    };

    let (metadata, rows) = reader.map_snapshot(|_, database, value| {
        let row = match database {
            ACCOUNT_BALANCES_DATABASE => {
                let root = value.balance()?;
                let raw = root.balance();
                MappedAccountRow::Balance(
                    root.segment_key().to_owned(),
                    AccountBalanceCurrent {
                        asset: raw
                            .asset_code()
                            .filter(|value| !value.is_empty())
                            .unwrap_or(raw.asset_id())
                            .to_owned(),
                        total: decimal(raw.total(), "quantity"),
                        available: raw
                            .available()
                            .map(|value| decimal(value, "quantity"))
                            .unwrap_or_else(|| zero_decimal("quantity")),
                        reserved: raw
                            .locked()
                            .map(|value| decimal(value, "quantity"))
                            .unwrap_or_else(|| zero_decimal("quantity")),
                    },
                )
            },
            ACCOUNT_COLLATERAL_DATABASE => {
                let root = value.collateral()?;
                let raw = root.balance();
                MappedAccountRow::Collateral(AccountCollateralCurrent {
                    account_id: root.account_id().to_owned(),
                    segment_key: root.segment_key().to_owned(),
                    asset: raw
                        .asset_code()
                        .filter(|value| !value.is_empty())
                        .unwrap_or(raw.asset_id())
                        .to_owned(),
                    total: decimal(raw.total(), "quantity"),
                    available: raw.available().map(|value| decimal(value, "quantity")),
                    locked: raw.locked().map(|value| decimal(value, "quantity")),
                    borrowed: raw.borrowed().map(|value| decimal(value, "quantity")),
                    interest: raw.interest().map(|value| decimal(value, "quantity")),
                })
            },
            ACCOUNT_POSITIONS_DATABASE => {
                let root = value.position()?;
                let raw = root.position();
                let quantity = decimal(raw.quantity(), "signed_quantity");
                let mark = raw.mark_price().map(|value| decimal(value, "price"));
                MappedAccountRow::Position(
                    root.segment_key().to_owned(),
                    AccountPositionCurrent {
                        instrument_id: raw.instrument_id().to_owned(),
                        quantity: quantity.clone(),
                        position_side: position_side(raw.position_side().0).to_owned(),
                        average_price: raw.average_price().map(|value| decimal(value, "price")),
                        market_value: mark.map(|mark| multiply(&quantity, &mark)).transpose()?,
                        unrealized_pnl: raw.unrealized_pnl().map(|value| decimal(value, "money")),
                    },
                )
            },
            ACCOUNT_EARN_HOLDINGS_DATABASE => {
                let root = value.earn_holding()?;
                let raw = root.holding();
                MappedAccountRow::EarnHolding(
                    root.segment_key().to_owned(),
                    AccountEarnHoldingCurrent {
                        holding_key: raw.holding_key().to_owned(),
                        participant_position_id: optional_text(raw.participant_position_id()),
                        product_id: raw.product_id().to_owned(),
                        asset: raw.asset().to_owned(),
                        principal: decimal(raw.principal(), "quantity"),
                        redeemable: raw.redeemable().map(|value| decimal(value, "quantity")),
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
                    },
                )
            },
            ACCOUNT_VALUATIONS_DATABASE => {
                let root = value.valuation()?;
                MappedAccountRow::Valuation(
                    root.segment_key().to_owned(),
                    root.valuation()
                        .equity()
                        .map(|value| decimal(value, "money")),
                )
            },
            ACCOUNT_SEGMENTS_DATABASE => {
                let root = value.segment()?;
                let state = root.state();
                MappedAccountRow::Segment(AccountSegmentCurrent {
                    account_id: root.account_id().to_owned(),
                    segment_key: state.segment_key().to_owned(),
                    broker: state.broker().to_owned(),
                    environment: state.environment().to_owned(),
                    account_model: match state.observed_account_model().0 {
                        1 => Some("cash"),
                        2 => Some("margin"),
                        3 => Some("portfolio_margin"),
                        _ => None,
                    }
                    .map(str::to_owned),
                    equity: None,
                    balances: Vec::new(),
                    positions: Vec::new(),
                    earn_holdings: Vec::new(),
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
                })
            },
            ACCOUNT_OBSERVED_ORDERS_DATABASE => {
                let root = value.observed_order()?;
                let raw = root.order();
                MappedAccountRow::ObservedOrder(AccountObservedOrderCurrent {
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
                    quantity: decimal(raw.quantity(), "quantity"),
                    filled_quantity: decimal(raw.filled_quantity(), "quantity"),
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
                })
            },
            _ => unreachable!("Account map_snapshot returns only declared databases"),
        };
        Ok(row)
    })?;

    let mut balances: BTreeMap<String, Vec<AccountBalanceCurrent>> = BTreeMap::new();
    let mut positions: BTreeMap<String, Vec<AccountPositionCurrent>> = BTreeMap::new();
    let mut holdings: BTreeMap<String, Vec<AccountEarnHoldingCurrent>> = BTreeMap::new();
    let mut valuations = BTreeMap::new();
    let mut segments = Vec::new();
    let mut collateral = Vec::new();
    let mut observed_orders = Vec::new();
    for row in rows.into_values().flatten() {
        match row {
            MappedAccountRow::Segment(value) => segments.push(value),
            MappedAccountRow::Balance(key, value) => balances.entry(key).or_default().push(value),
            MappedAccountRow::Collateral(value) => collateral.push(value),
            MappedAccountRow::Position(key, value) => positions.entry(key).or_default().push(value),
            MappedAccountRow::EarnHolding(key, value) => {
                holdings.entry(key).or_default().push(value)
            },
            MappedAccountRow::Valuation(key, value) => {
                valuations.insert(key, value);
            },
            MappedAccountRow::ObservedOrder(value) => observed_orders.push(value),
        }
    }
    for segment in &mut segments {
        segment.equity = valuations.remove(&segment.segment_key).flatten();
        segment.balances = balances.remove(&segment.segment_key).unwrap_or_default();
        segment.positions = positions.remove(&segment.segment_key).unwrap_or_default();
        segment.earn_holdings = holdings.remove(&segment.segment_key).unwrap_or_default();
    }
    let account_id = segments
        .first()
        .map(|segment| segment.account_id.clone())
        .unwrap_or_default();
    let generation = segments
        .iter()
        .map(|segment| segment.generation)
        .max()
        .unwrap_or(0);
    Ok(AccountCurrentSnapshot {
        account_id,
        segments,
        collateral,
        generation,
        event_sequence: metadata.applied_event_sequence,
        observed_orders,
    })
}

fn account_event_metadata(
    value: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
) -> PyResult<AccountEventMetadata> {
    let EventMetadataOwned {
        event_id,
        stream_id,
        sequence,
        producer_id,
        producer_incarnation,
        workspace_id,
        launch_id,
        instance_id,
        correlation_id,
        causation_id,
        occurred_at_unix_nanos,
        published_at_unix_nanos,
    } = decode_event_metadata(value)
        .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))?;
    Ok(AccountEventMetadata {
        event_id: event_id.to_string(),
        stream_id: stream_id.to_string(),
        sequence: sequence.get(),
        producer: producer_id.to_string(),
        producer_incarnation,
        workspace_id: workspace_id.to_string(),
        launch_id: launch_id.map(|item| item.to_string()),
        instance_id: instance_id.map(|item| item.to_string()),
        correlation_id: correlation_id.map(|item| item.to_string()),
        causation_id: causation_id.map(|item| item.to_string()),
        occurred_at_unix_nanos: occurred_at_unix_nanos.get(),
        published_at_unix_nanos: published_at_unix_nanos.get(),
    })
}

fn project_event(py: Python<'_>, value: RustAccountEvent) -> PyResult<AccountEvent> {
    let kind = value.kind().as_str().to_owned();
    let metadata = AccountEventMetadata {
        event_id: value.metadata.event_id.to_string(),
        stream_id: value.metadata.stream_id,
        sequence: value.metadata.sequence.get(),
        producer: value.metadata.producer_id.to_string(),
        producer_incarnation: value.metadata.producer_incarnation,
        workspace_id: value.metadata.workspace_id.to_string(),
        launch_id: value.metadata.launch_id.map(|item| item.to_string()),
        instance_id: value.metadata.instance_id.map(|item| item.to_string()),
        correlation_id: value.metadata.correlation_id,
        causation_id: value.metadata.causation_id,
        occurred_at_unix_nanos: value.metadata.occurred_at_unix_nanos.get(),
        published_at_unix_nanos: value.metadata.published_at_unix_nanos.get(),
    };
    let provenance = value.provenance.map(|item| AccountEventProvenance {
        source_id: item.source_id.to_string(),
        provider_event_id: item.provider_event_id,
        provider_sequence: item.provider_sequence.map(|value| value.get()),
        provider_occurred_at_unix_nanos: item
            .provider_occurred_at_unix_nanos
            .map(|value| value.get()),
        provider_received_at_unix_nanos: item
            .provider_received_at_unix_nanos
            .map(|value| value.get()),
    });
    macro_rules! data {
        ($value:expr) => {
            Py::new(py, $value)?.into_any()
        };
    }
    let data = match value.change {
        RustAccountChange::BalanceUpserted(item) => {
            data!(AccountBalanceEvent {
                asset_id: item.asset_id.to_string(),
                asset: item.asset_code.unwrap_or_else(|| item.asset_id.to_string()),
                total: decimal_parts(item.total, "quantity"),
                available: item.available.map(|value| decimal_parts(value, "quantity")),
                locked: item.locked.map(|value| decimal_parts(value, "quantity")),
                borrowed: item.borrowed.map(|value| decimal_parts(value, "quantity")),
                interest: item.interest.map(|value| decimal_parts(value, "quantity")),
            })
        },
        RustAccountChange::BalanceRemoved { asset_id } => data!(AccountBalanceRemovedEvent {
            asset_id: asset_id.to_string(),
        }),
        RustAccountChange::PositionUpserted(item) => {
            data!(AccountPositionEvent {
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                position_side: item.position_side.as_str().to_owned(),
                quantity: decimal_parts(item.quantity, "signed_quantity"),
                average_price: item
                    .average_price
                    .map(|value| decimal_parts(value, "price")),
                mark_price: item.mark_price.map(|value| decimal_parts(value, "price")),
                unrealized_pnl: item
                    .unrealized_pnl
                    .map(|value| decimal_parts(value, "money")),
                realized_pnl: item.realized_pnl.map(|value| decimal_parts(value, "money")),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            })
        },
        RustAccountChange::PositionRemoved(item) => {
            data!(AccountPositionRemovedEvent {
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                position_side: item.position_side.as_str().to_owned(),
            })
        },
        RustAccountChange::EarnHoldingUpserted(item) => {
            data!(AccountEarnHoldingEvent {
                holding_key: item.holding_key,
                participant_position_id: item.participant_position_id,
                product_id: item.product_id.to_string(),
                asset: item.asset,
                principal: decimal_parts(item.principal, "quantity"),
                redeemable: item
                    .redeemable
                    .map(|value| decimal_parts(value, "quantity")),
                state: item.state.as_str().to_owned(),
                participant_state: item.participant_state,
                liquidity: item.liquidity.as_str().to_owned(),
                notice_seconds: item.notice_seconds,
                matures_at_unix_nanos: item.matures_at_unix_nanos.map(|value| value.get()),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            })
        },
        RustAccountChange::EarnHoldingRemoved { holding_key } => {
            data!(AccountEarnHoldingRemovedEvent { holding_key })
        },
        RustAccountChange::ValuationChanged(item) => {
            data!(AccountValuationEvent {
                valuation_asset_id: item.valuation_asset_id.map(|value| value.to_string()),
                equity: item.equity.map(|value| decimal_parts(value, "money")),
                initial_equity: item
                    .initial_equity
                    .map(|value| decimal_parts(value, "money")),
                net_profit: item.net_profit.map(|value| decimal_parts(value, "money")),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            })
        },
        RustAccountChange::StatusChanged(item) => {
            data!(AccountStatusEvent {
                status: item.status.as_str().to_owned(),
                freshness: item.freshness.as_str().to_owned(),
                reason: item.reason,
                trading_enabled: matches!(
                    item.status,
                    kairos_account_contract::AccountStatus::Active
                ),
            })
        },
        RustAccountChange::ObservedOrderUpserted(item) => {
            data!(AccountObservedOrderEvent {
                observation_id: item.observation_id,
                source_id: item.source_id.to_string(),
                execution_order_id: item.execution_order_id.map(|value| value.to_string()),
                remote_order_id: item.remote_order_id.map(|value| value.to_string()),
                instrument_id: item.instrument_id.to_string(),
                market_id: item.market_id.to_string(),
                side: match item.side {
                    kairos_primitives::execution::OrderSide::Buy => "buy",
                    kairos_primitives::execution::OrderSide::Sell => "sell",
                }
                .to_owned(),
                quantity: decimal_parts(item.quantity, "quantity"),
                filled_quantity: decimal_parts(item.filled_quantity, "quantity"),
                status: item.status.as_str().to_owned(),
                observed_at_unix_nanos: item.observed_at_unix_nanos.map(|value| value.get()),
            })
        },
        RustAccountChange::ObservedOrderRemoved(item) => {
            data!(AccountObservedOrderRemovedEvent {
                observation_id: item.observation_id,
                execution_order_id: item.execution_order_id.map(|value| value.to_string()),
                remote_order_id: item.remote_order_id.map(|value| value.to_string()),
            })
        },
    };
    Ok(AccountEvent {
        metadata,
        kind,
        data,
        account_id: value.account_id.to_string(),
        segment_key: value.segment_key.to_string(),
        provenance,
    })
}

fn decimal(value: &Decimal64, semantic_type: &'static str) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type,
    }
}
fn decimal_parts(
    value: kairos_primitives::decimal::DecimalParts,
    semantic_type: &'static str,
) -> NativeDecimal {
    NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type,
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
fn zero_decimal(semantic_type: &'static str) -> NativeDecimal {
    NativeDecimal {
        mantissa: 0,
        scale: 0,
        semantic_type,
    }
}
fn position_side(value: u8) -> &'static str {
    match value {
        2 => "long",
        3 => "short",
        _ => "net",
    }
}
fn multiply(left: &NativeDecimal, right: &NativeDecimal) -> Result<NativeDecimal, ContractError> {
    let quantity = SignedQuantity::new(left.mantissa, left.scale)
        .map_err(|error| ContractError::Invalid(error.to_string()))?;
    let price = Price::new(right.mantissa, right.scale)
        .map_err(|error| ContractError::Invalid(error.to_string()))?;
    let value: Money = quantity
        .checked_mul(price)
        .map_err(|error| ContractError::Invalid(error.to_string()))?;
    Ok(NativeDecimal {
        mantissa: value.mantissa(),
        scale: value.scale(),
        semantic_type: "money",
    })
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

fn semantic_parts(value: &Bound<'_, PyAny>, expected: &str) -> PyResult<DecimalParts> {
    if let Ok(text) = value.extract::<String>() {
        return text
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            });
    }
    let semantic_type = value
        .getattr("semantic_type")
        .and_then(|value| value.extract::<String>())
        .map_err(|_| {
            AccountInvalidInputError::new_err(format!(
                "Account {expected} requires exact text or a semantic decimal value"
            ))
        })?;
    if semantic_type != expected {
        return Err(AccountInvalidInputError::new_err(format!(
            "Account {expected} cannot be constructed from {semantic_type}"
        )));
    }
    DecimalParts::new(
        value.getattr("mantissa")?.extract::<i64>()?,
        value.getattr("scale")?.extract::<u8>()?,
    )
    .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn price_input(value: &Bound<'_, PyAny>) -> PyResult<Price> {
    let parts = semantic_parts(value, "price")?;
    Price::new(parts.mantissa(), parts.scale())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn quantity_input(value: &Bound<'_, PyAny>) -> PyResult<Quantity> {
    let parts = semantic_parts(value, "quantity")?;
    Quantity::new(parts.mantissa(), parts.scale())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))
}

fn signed_quantity_input(value: &Bound<'_, PyAny>) -> PyResult<SignedQuantity> {
    let parts = semantic_parts(value, "signed_quantity")?;
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

fn simulation_event<T: PyClass<BaseType = PyAny>>(
    py: Python<'_>,
    account_id: String,
    segment_key: String,
    sequence: u64,
    launch_id: Option<String>,
    instance_id: Option<String>,
    producer_incarnation: u64,
    kind: &'static str,
    data: T,
) -> PyResult<AccountEvent> {
    AccountId::new(account_id.clone())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
    SegmentKey::new(segment_key.clone())
        .map_err(|error| AccountInvalidInputError::new_err(error.to_string()))?;
    if producer_incarnation == 0 {
        return Err(AccountInvalidInputError::new_err(
            "producer_incarnation must be positive",
        ));
    }
    if launch_id.is_some() != instance_id.is_some() {
        return Err(AccountInvalidInputError::new_err(
            "launch_id and instance_id must both be present or both be absent",
        ));
    }
    Ok(AccountEvent {
        metadata: AccountEventMetadata {
            event_id: format!("account:{account_id}:{sequence}"),
            stream_id: format!("account.events/account:{account_id}"),
            sequence,
            producer: "account".to_owned(),
            producer_incarnation,
            workspace_id: "simulation".to_owned(),
            launch_id,
            instance_id,
            correlation_id: None,
            causation_id: None,
            occurred_at_unix_nanos: sequence,
            published_at_unix_nanos: sequence,
        },
        kind: kind.to_owned(),
        data: Py::new(py, data)?.into_any(),
        account_id,
        segment_key,
        provenance: None,
    })
}

fn native_decimal(value: &str, semantic_type: &'static str) -> PyResult<NativeDecimal> {
    let parts: DecimalParts =
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                AccountInvalidInputError::new_err(error.to_string())
            })?;
    Ok(NativeDecimal {
        mantissa: parts.mantissa(),
        scale: parts.scale(),
        semantic_type,
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
fn decode_event(py: Python<'_>, payload: &[u8]) -> PyResult<AccountEvent> {
    kairos_account_contract::decode_event(payload)
        .map_err(|error| AccountInvalidEventError::new_err(error.to_string()))
        .and_then(|event| project_event(py, event))
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
    module.add_class::<AccountEventMetadata>()?;
    module.add_class::<AccountEventProvenance>()?;
    module.add_class::<AccountBalanceEvent>()?;
    module.add_class::<AccountBalanceRemovedEvent>()?;
    module.add_class::<AccountPositionEvent>()?;
    module.add_class::<AccountPositionRemovedEvent>()?;
    module.add_class::<AccountEarnHoldingEvent>()?;
    module.add_class::<AccountEarnHoldingRemovedEvent>()?;
    module.add_class::<AccountValuationEvent>()?;
    module.add_class::<AccountStatusEvent>()?;
    module.add_class::<AccountObservedOrderEvent>()?;
    module.add_class::<AccountObservedOrderRemovedEvent>()?;
    module.add_class::<AccountEvent>()?;
    module.add_class::<AccountLiveEventView>()?;
    module.add_class::<AccountLiveSubscription>()?;
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
