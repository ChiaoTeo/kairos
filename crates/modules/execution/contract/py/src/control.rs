use std::path::PathBuf;
use std::time::Duration;

use kairos_execution_contract::{
    AdvanceExecutionTimeRequest, CancelOrderRequest, CommandEnvelope, CompletionPolicy,
    ExecutionAlgorithmPolicyRequest, ExecutionAttemptEvidenceResponse, ExecutionBacktestBar,
    ExecutionBacktestEquityPoint, ExecutionBacktestFill, ExecutionBacktestMarketObservation,
    ExecutionBacktestMarketRequest, ExecutionBacktestMarketResponse, ExecutionBacktestMetrics,
    ExecutionBacktestObservationScope, ExecutionBacktestOrder, ExecutionBacktestOrderRequest,
    ExecutionBacktestOrderStatus, ExecutionBacktestQuote, ExecutionBacktestRequest,
    ExecutionBacktestRunResponse, ExecutionBacktestSimulationConfig,
    ExecutionBacktestSimulationFill, ExecutionBenchmarkKind, ExecutionBenchmarkRequest,
    ExecutionCommandStatus, ExecutionControlRpcClient, ExecutionHealthResponse,
    ExecutionIntentRequest, ExecutionOrderAuditEventResponse, ExecutionOrderAuditQuery,
    ExecutionOrderAuditResponse, ExecutionOrderLifecycle, ExecutionOrderOptionsRequest,
    ExecutionReconcileResponse, ExecutionRouteCandidateResponse, ExecutionRoutesQuery,
    ExecutionRoutesResponse, FailurePolicy, HedgePolicyRequest, IntentAdmissionEvidenceRequest,
    IntentLegRequest, IntentType, MakerExecutionPolicyRequest, PassiveLimitPolicyRequest,
    ReconcileExecutionRequest, ReplaceOrderRequest, SplitOrderPolicyRequest, SubmitIntentRequest,
    TwapPolicyRequest,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::{
    DecimalParts, Money, Price, Quantity, Rate, Ratio, SignedQuantity,
};
use kairos_primitives::execution::{ExecutionRouteId, LegId, OrderId, OrderSide, OrderType};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{Currency, InstrumentId, MarketId};
use kairos_primitives::risk::DecisionId;
use kairos_primitives::runtime::{
    ActorId, IdempotencyKey, InstanceId, LaunchId, RequestId, StrategyId, WorkspaceId,
};
use kairos_primitives::time::{DurationNanos, Sequence, UnixNanos};
use pyo3::create_exception;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use sha2::{Digest, Sha256};

use super::ExecutionInvalidInputError;

create_exception!(
    _native_execution_contract,
    ExecutionControlUnavailableError,
    PyRuntimeError
);
create_exception!(
    _native_execution_contract,
    ExecutionControlRejectedError,
    PyRuntimeError
);

#[pyclass(
    name = "SplitOrderPolicyRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeSplitOrderPolicyRequest {
    inner: SplitOrderPolicyRequest,
}

#[pymethods]
impl NativeSplitOrderPolicyRequest {
    #[new]
    #[pyo3(signature = (*, max_child_quantity=None, child_count=None, min_child_quantity=None))]
    fn new(
        max_child_quantity: Option<String>,
        child_count: Option<u32>,
        min_child_quantity: Option<String>,
    ) -> PyResult<Self> {
        if child_count == Some(0) {
            return Err(ExecutionInvalidInputError::new_err(
                "child_count must be positive",
            ));
        }
        Ok(Self {
            inner: SplitOrderPolicyRequest {
                max_child_quantity: max_child_quantity.map(|v| parse_quantity(&v)).transpose()?,
                child_count,
                min_child_quantity: min_child_quantity.map(|v| parse_quantity(&v)).transpose()?,
            },
        })
    }
}

#[pyclass(
    name = "MakerExecutionPolicyRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeMakerExecutionPolicyRequest {
    inner: MakerExecutionPolicyRequest,
}

#[pymethods]
impl NativeMakerExecutionPolicyRequest {
    #[new]
    #[pyo3(signature = (*, max_inventory_abs=None, target_inventory=None, max_quote_age_nanos=None))]
    fn new(
        max_inventory_abs: Option<String>,
        target_inventory: Option<String>,
        max_quote_age_nanos: Option<u64>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: MakerExecutionPolicyRequest {
                max_inventory_abs: max_inventory_abs.map(|v| signed_quantity(&v)).transpose()?,
                target_inventory: target_inventory.map(|v| signed_quantity(&v)).transpose()?,
                max_quote_age: max_quote_age_nanos.map(DurationNanos::new),
            },
        })
    }
}

#[pyclass(
    name = "ExecutionOrderOptionsRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionOrderOptionsRequest {
    inner: ExecutionOrderOptionsRequest,
}

#[pymethods]
impl NativeExecutionOrderOptionsRequest {
    #[new]
    #[pyo3(signature = (*, time_in_force=None, reduce_only=None, post_only=None, position_side=None, quote_asset=None, wallet_type=None, trading_session=None, tokenize=None, split=None, maker=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        time_in_force: Option<String>,
        reduce_only: Option<bool>,
        post_only: Option<bool>,
        position_side: Option<String>,
        quote_asset: Option<String>,
        wallet_type: Option<String>,
        trading_session: Option<String>,
        tokenize: Option<bool>,
        split: Option<Py<NativeSplitOrderPolicyRequest>>,
        maker: Option<Py<NativeMakerExecutionPolicyRequest>>,
    ) -> Self {
        Self {
            inner: ExecutionOrderOptionsRequest {
                time_in_force,
                reduce_only,
                post_only,
                position_side,
                quote_asset,
                wallet_type,
                trading_session,
                tokenize,
                split: split.map(|v| v.borrow(py).inner.clone()),
                maker: maker.map(|v| v.borrow(py).inner.clone()),
            },
        }
    }

    #[getter]
    fn time_in_force(&self) -> Option<&str> {
        self.inner.time_in_force.as_deref()
    }
    #[getter]
    fn reduce_only(&self) -> Option<bool> {
        self.inner.reduce_only
    }
    #[getter]
    fn post_only(&self) -> Option<bool> {
        self.inner.post_only
    }
    #[getter]
    fn has_split(&self) -> bool {
        self.inner.split.is_some()
    }
    #[getter]
    fn has_maker(&self) -> bool {
        self.inner.maker.is_some()
    }
}

#[pyclass(
    name = "IntentLegRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeIntentLegRequest {
    inner: IntentLegRequest,
}

#[pymethods]
impl NativeIntentLegRequest {
    #[new]
    #[pyo3(signature = (*, leg_id, account_id, segment_key, instrument_id, side, quantity, options, market_id=None, execution_route_id=None, limit_price=None, target_position=false))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        leg_id: String,
        account_id: String,
        segment_key: String,
        instrument_id: String,
        side: String,
        quantity: String,
        options: PyRef<'_, NativeExecutionOrderOptionsRequest>,
        market_id: Option<String>,
        execution_route_id: Option<String>,
        limit_price: Option<String>,
        target_position: bool,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: IntentLegRequest {
                leg_id: LegId::new(leg_id).map_err(value_error)?,
                account_id: AccountId::new(account_id).map_err(value_error)?,
                segment_key: SegmentKey::new(segment_key).map_err(value_error)?,
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                market_id: market_id
                    .map(MarketId::new)
                    .transpose()
                    .map_err(value_error)?,
                execution_route_id: execution_route_id
                    .map(ExecutionRouteId::new)
                    .transpose()
                    .map_err(value_error)?,
                side: side.parse().map_err(value_error)?,
                quantity: parse_quantity(&quantity)?,
                limit_price: limit_price.map(|v| parse_price(&v)).transpose()?,
                target_position,
                options: options.inner.clone(),
            },
        })
    }

    #[getter]
    fn leg_id(&self) -> String {
        self.inner.leg_id.to_string()
    }
    #[getter]
    fn account_id(&self) -> String {
        self.inner.account_id.to_string()
    }
    #[getter]
    fn segment_key(&self) -> String {
        self.inner.segment_key.to_string()
    }
    #[getter]
    fn instrument_id(&self) -> String {
        self.inner.instrument_id.to_string()
    }
    #[getter]
    fn market_id(&self) -> Option<String> {
        self.inner.market_id.as_ref().map(ToString::to_string)
    }
    #[getter]
    fn execution_route_id(&self) -> Option<String> {
        self.inner
            .execution_route_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn side(&self) -> &'static str {
        match self.inner.side {
            OrderSide::Buy => "buy",
            OrderSide::Sell => "sell",
        }
    }
    #[getter]
    fn quantity(&self) -> String {
        self.inner.quantity.to_string()
    }
    #[getter]
    fn limit_price(&self) -> Option<String> {
        self.inner.limit_price.as_ref().map(ToString::to_string)
    }
    #[getter]
    fn target_position(&self) -> bool {
        self.inner.target_position
    }
    #[getter]
    fn options(&self) -> NativeExecutionOrderOptionsRequest {
        NativeExecutionOrderOptionsRequest {
            inner: self.inner.options.clone(),
        }
    }
}

#[pyclass(
    name = "ExecutionBenchmarkRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBenchmarkRequest {
    inner: ExecutionBenchmarkRequest,
}

#[pymethods]
impl NativeExecutionBenchmarkRequest {
    #[new]
    #[pyo3(signature = (*, instrument_id, market_id, price, observed_at_unix_nanos, leg_id=None, kind="arrival"))]
    fn new(
        instrument_id: String,
        market_id: String,
        price: String,
        observed_at_unix_nanos: u64,
        leg_id: Option<String>,
        kind: &str,
    ) -> PyResult<Self> {
        if kind != "arrival" {
            return Err(ExecutionInvalidInputError::new_err(
                "unsupported Execution benchmark kind",
            ));
        }
        Ok(Self {
            inner: ExecutionBenchmarkRequest {
                kind: ExecutionBenchmarkKind::Arrival,
                leg_id: leg_id.map(LegId::new).transpose().map_err(value_error)?,
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                market_id: MarketId::new(market_id).map_err(value_error)?,
                price: parse_price(&price)?,
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
            },
        })
    }
    #[getter]
    fn kind(&self) -> &'static str {
        "arrival"
    }
    #[getter]
    fn leg_id(&self) -> Option<String> {
        self.inner.leg_id.as_ref().map(ToString::to_string)
    }
    #[getter]
    fn instrument_id(&self) -> String {
        self.inner.instrument_id.to_string()
    }
    #[getter]
    fn market_id(&self) -> String {
        self.inner.market_id.to_string()
    }
    #[getter]
    fn price(&self) -> String {
        self.inner.price.to_string()
    }
    #[getter]
    fn observed_at_unix_nanos(&self) -> u64 {
        self.inner.observed_at_unix_nanos.get()
    }
}

#[pyclass(
    name = "ExecutionAlgorithmPolicyRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionAlgorithmPolicyRequest {
    inner: ExecutionAlgorithmPolicyRequest,
}

#[pymethods]
impl NativeExecutionAlgorithmPolicyRequest {
    #[staticmethod]
    fn immediate() -> Self {
        Self {
            inner: ExecutionAlgorithmPolicyRequest::Immediate,
        }
    }

    #[staticmethod]
    fn twap(slice_count: u32, slice_interval_nanos: u64) -> PyResult<Self> {
        if slice_count == 0 || slice_interval_nanos == 0 {
            return Err(ExecutionInvalidInputError::new_err(
                "TWAP count and interval must be positive",
            ));
        }
        Ok(Self {
            inner: ExecutionAlgorithmPolicyRequest::Twap(TwapPolicyRequest {
                slice_count,
                slice_interval: DurationNanos::new(slice_interval_nanos),
            }),
        })
    }

    #[staticmethod]
    fn passive_limit(reprice_interval_nanos: u64, max_quote_age_nanos: u64) -> PyResult<Self> {
        if reprice_interval_nanos == 0 || max_quote_age_nanos == 0 {
            return Err(ExecutionInvalidInputError::new_err(
                "passive-limit durations must be positive",
            ));
        }
        Ok(Self {
            inner: ExecutionAlgorithmPolicyRequest::PassiveLimit(PassiveLimitPolicyRequest {
                reprice_interval: DurationNanos::new(reprice_interval_nanos),
                max_quote_age: DurationNanos::new(max_quote_age_nanos),
            }),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, leader_leg_id, hedge_leg_id, ratio, contract_multiplier, max_unhedged_quantity, compensate_on_failure, max_compensation_attempts, max_unhedged_duration_nanos=None, fallback_execution_route_ids=Vec::new()))]
    #[allow(clippy::too_many_arguments)]
    fn maker_taker_hedge(
        leader_leg_id: String,
        hedge_leg_id: String,
        ratio: String,
        contract_multiplier: String,
        max_unhedged_quantity: String,
        compensate_on_failure: bool,
        max_compensation_attempts: u32,
        max_unhedged_duration_nanos: Option<u64>,
        fallback_execution_route_ids: Vec<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ExecutionAlgorithmPolicyRequest::MakerTakerHedge(HedgePolicyRequest {
                leader_leg_id: LegId::new(leader_leg_id).map_err(value_error)?,
                hedge_leg_id: LegId::new(hedge_leg_id).map_err(value_error)?,
                ratio: parse_ratio(&ratio)?,
                contract_multiplier: parse_ratio(&contract_multiplier)?,
                max_unhedged_quantity: parse_quantity(&max_unhedged_quantity)?,
                max_unhedged_duration: max_unhedged_duration_nanos.map(DurationNanos::new),
                fallback_execution_route_ids: fallback_execution_route_ids
                    .into_iter()
                    .map(ExecutionRouteId::new)
                    .collect::<Result<_, _>>()
                    .map_err(value_error)?,
                compensate_on_failure,
                max_compensation_attempts,
            }),
        })
    }

    #[getter]
    fn kind(&self) -> &'static str {
        match self.inner {
            ExecutionAlgorithmPolicyRequest::Immediate => "immediate",
            ExecutionAlgorithmPolicyRequest::Twap(_) => "twap",
            ExecutionAlgorithmPolicyRequest::PassiveLimit(_) => "passive_limit",
            ExecutionAlgorithmPolicyRequest::MakerTakerHedge(_) => "maker_taker_hedge",
        }
    }
}

#[pyclass(
    name = "ExecutionIntentRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionIntentRequest {
    inner: ExecutionIntentRequest,
}

#[pymethods]
impl NativeExecutionIntentRequest {
    #[new]
    #[pyo3(signature = (*, intent_id, strategy_id, launch_id, instance_id, instrument_id, account_ids, segment_key, target_quantity, reason, intent_type, algorithm, order_options, strategy_decision_id=None, market_id=None, execution_route_id=None, limit_price=None, source_snapshot_id=None, source_event_sequence=None, source_event_time_unix_nanos=None, completion_policy="all_legs_satisfied", failure_policy="cancel_remaining", legs=Vec::new(), execution_benchmarks=Vec::new(), deadline_unix_nanos=None, min_edge_bps=None, max_slippage_bps=None, estimated_fee_bps=None, minimum_net_credit=None, maximum_loss=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        intent_id: String,
        strategy_id: String,
        launch_id: String,
        instance_id: String,
        instrument_id: String,
        account_ids: Vec<String>,
        segment_key: String,
        target_quantity: String,
        reason: String,
        intent_type: &str,
        algorithm: Py<NativeExecutionAlgorithmPolicyRequest>,
        order_options: Py<NativeExecutionOrderOptionsRequest>,
        strategy_decision_id: Option<String>,
        market_id: Option<String>,
        execution_route_id: Option<String>,
        limit_price: Option<String>,
        source_snapshot_id: Option<String>,
        source_event_sequence: Option<u64>,
        source_event_time_unix_nanos: Option<u64>,
        completion_policy: &str,
        failure_policy: &str,
        legs: Vec<Py<NativeIntentLegRequest>>,
        execution_benchmarks: Vec<Py<NativeExecutionBenchmarkRequest>>,
        deadline_unix_nanos: Option<u64>,
        min_edge_bps: Option<u32>,
        max_slippage_bps: Option<u32>,
        estimated_fee_bps: Option<u32>,
        minimum_net_credit: Option<String>,
        maximum_loss: Option<String>,
    ) -> PyResult<Self> {
        if account_ids.is_empty() {
            return Err(ExecutionInvalidInputError::new_err(
                "Execution intent requires account_ids",
            ));
        }
        Ok(Self {
            inner: ExecutionIntentRequest {
                intent_id: kairos_primitives::execution::IntentId::new(intent_id)
                    .map_err(value_error)?,
                strategy_decision_id: strategy_decision_id
                    .map(DecisionId::new)
                    .transpose()
                    .map_err(value_error)?,
                strategy_id: StrategyId::new(strategy_id).map_err(value_error)?,
                launch_id: LaunchId::new(launch_id).map_err(value_error)?,
                instance_id: InstanceId::new(instance_id).map_err(value_error)?,
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                market_id: market_id
                    .map(MarketId::new)
                    .transpose()
                    .map_err(value_error)?,
                execution_route_id: execution_route_id
                    .map(ExecutionRouteId::new)
                    .transpose()
                    .map_err(value_error)?,
                account_ids: account_ids
                    .into_iter()
                    .map(AccountId::new)
                    .collect::<Result<_, _>>()
                    .map_err(value_error)?,
                segment_key: SegmentKey::new(segment_key).map_err(value_error)?,
                target_quantity: parse_quantity(&target_quantity)?,
                limit_price: limit_price.map(|v| parse_price(&v)).transpose()?,
                source_snapshot_id,
                source_event_sequence: source_event_sequence.map(Sequence::new),
                source_event_time_unix_nanos: source_event_time_unix_nanos.map(UnixNanos::new),
                reason,
                intent_type: parse_intent_type(intent_type)?,
                algorithm: algorithm.borrow(py).inner.clone(),
                completion_policy: parse_completion_policy(completion_policy)?,
                failure_policy: parse_failure_policy(failure_policy)?,
                legs: legs
                    .into_iter()
                    .map(|v| v.borrow(py).inner.clone())
                    .collect(),
                execution_benchmarks: execution_benchmarks
                    .into_iter()
                    .map(|v| v.borrow(py).inner.clone())
                    .collect(),
                deadline_unix_nanos: deadline_unix_nanos.map(UnixNanos::new),
                min_edge_bps,
                max_slippage_bps,
                estimated_fee_bps,
                minimum_net_credit: minimum_net_credit.map(|v| parse_money(&v)).transpose()?,
                maximum_loss: maximum_loss.map(|v| parse_money(&v)).transpose()?,
                order_options: order_options.borrow(py).inner.clone(),
            },
        })
    }

    #[getter]
    fn intent_id(&self) -> String {
        self.inner.intent_id.to_string()
    }
    #[getter]
    fn strategy_decision_id(&self) -> Option<String> {
        self.inner
            .strategy_decision_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn strategy_id(&self) -> String {
        self.inner.strategy_id.to_string()
    }
    #[getter]
    fn launch_id(&self) -> String {
        self.inner.launch_id.to_string()
    }
    #[getter]
    fn instance_id(&self) -> String {
        self.inner.instance_id.to_string()
    }
    #[getter]
    fn instrument_id(&self) -> String {
        self.inner.instrument_id.to_string()
    }
    #[getter]
    fn market_id(&self) -> Option<String> {
        self.inner.market_id.as_ref().map(ToString::to_string)
    }
    #[getter]
    fn execution_route_id(&self) -> Option<String> {
        self.inner
            .execution_route_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn account_ids(&self) -> Vec<String> {
        self.inner
            .account_ids
            .iter()
            .map(ToString::to_string)
            .collect()
    }
    #[getter]
    fn segment_key(&self) -> String {
        self.inner.segment_key.to_string()
    }
    #[getter]
    fn target_quantity(&self) -> String {
        self.inner.target_quantity.to_string()
    }
    #[getter]
    fn limit_price(&self) -> Option<String> {
        self.inner.limit_price.as_ref().map(ToString::to_string)
    }
    #[getter]
    fn reason(&self) -> &str {
        &self.inner.reason
    }
    #[getter]
    fn intent_type(&self) -> &'static str {
        intent_type(self.inner.intent_type)
    }
    #[getter]
    fn algorithm(&self) -> NativeExecutionAlgorithmPolicyRequest {
        NativeExecutionAlgorithmPolicyRequest {
            inner: self.inner.algorithm.clone(),
        }
    }
    #[getter]
    fn completion_policy(&self) -> &'static str {
        completion_policy(self.inner.completion_policy)
    }
    #[getter]
    fn failure_policy(&self) -> &'static str {
        failure_policy(self.inner.failure_policy)
    }
    #[getter]
    fn legs(&self) -> Vec<NativeIntentLegRequest> {
        self.inner
            .legs
            .iter()
            .cloned()
            .map(|inner| NativeIntentLegRequest { inner })
            .collect()
    }
    #[getter]
    fn execution_benchmarks(&self) -> Vec<NativeExecutionBenchmarkRequest> {
        self.inner
            .execution_benchmarks
            .iter()
            .cloned()
            .map(|inner| NativeExecutionBenchmarkRequest { inner })
            .collect()
    }
}

#[pyclass(
    name = "IntentAdmissionEvidenceRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeIntentAdmissionEvidenceRequest {
    inner: IntentAdmissionEvidenceRequest,
}

#[pymethods]
impl NativeIntentAdmissionEvidenceRequest {
    #[new]
    #[pyo3(signature = (*, source, decision_id, outcome, original_intent, effective_intent))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        source: String,
        decision_id: String,
        outcome: String,
        original_intent: PyRef<'_, NativeExecutionIntentRequest>,
        effective_intent: PyRef<'_, NativeExecutionIntentRequest>,
    ) -> PyResult<Self> {
        let original = original_intent.inner.clone();
        let effective = effective_intent.inner.clone();
        let original_hash = typed_hash(&original)?;
        let effective_hash = typed_hash(&effective)?;
        Ok(Self {
            inner: IntentAdmissionEvidenceRequest {
                source,
                decision_id: DecisionId::new(decision_id).map_err(value_error)?,
                outcome,
                original_intent: original,
                effective_intent: effective,
                original_hash,
                effective_hash,
            },
        })
    }
    #[getter]
    fn source(&self) -> &str {
        &self.inner.source
    }
    #[getter]
    fn decision_id(&self) -> String {
        self.inner.decision_id.to_string()
    }
    #[getter]
    fn outcome(&self) -> &str {
        &self.inner.outcome
    }
    #[getter]
    fn original_hash(&self) -> &str {
        &self.inner.original_hash
    }
    #[getter]
    fn effective_hash(&self) -> &str {
        &self.inner.effective_hash
    }
}

#[pyclass(
    name = "SubmitIntentRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeSubmitIntentRequest {
    inner: SubmitIntentRequest,
}

#[pymethods]
impl NativeSubmitIntentRequest {
    #[new]
    #[pyo3(signature = (*, intent, command_id=None, idempotency_key=None, caller_id=None, workspace_id=None, admission_evidence=None))]
    fn new(
        py: Python<'_>,
        intent: Py<NativeExecutionIntentRequest>,
        command_id: Option<String>,
        idempotency_key: Option<String>,
        caller_id: Option<String>,
        workspace_id: Option<String>,
        admission_evidence: Option<Py<NativeIntentAdmissionEvidenceRequest>>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: SubmitIntentRequest {
                envelope: CommandEnvelope {
                    command_id: command_id
                        .map(RequestId::new)
                        .transpose()
                        .map_err(value_error)?,
                    idempotency_key: idempotency_key
                        .map(IdempotencyKey::new)
                        .transpose()
                        .map_err(value_error)?,
                    caller_id: caller_id
                        .map(ActorId::new)
                        .transpose()
                        .map_err(value_error)?,
                    workspace_id: workspace_id
                        .map(WorkspaceId::new)
                        .transpose()
                        .map_err(value_error)?,
                },
                intent: intent.borrow(py).inner.clone(),
                admission_evidence: admission_evidence.map(|v| v.borrow(py).inner.clone()),
            },
        })
    }
    #[getter]
    fn command_id(&self) -> Option<String> {
        self.inner
            .envelope
            .command_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn idempotency_key(&self) -> Option<String> {
        self.inner
            .envelope
            .idempotency_key
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn caller_id(&self) -> Option<String> {
        self.inner
            .envelope
            .caller_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn workspace_id(&self) -> Option<String> {
        self.inner
            .envelope
            .workspace_id
            .as_ref()
            .map(ToString::to_string)
    }
    #[getter]
    fn intent(&self) -> NativeExecutionIntentRequest {
        NativeExecutionIntentRequest {
            inner: self.inner.intent.clone(),
        }
    }
    #[getter]
    fn admission_evidence(&self) -> Option<NativeIntentAdmissionEvidenceRequest> {
        self.inner
            .admission_evidence
            .clone()
            .map(|inner| NativeIntentAdmissionEvidenceRequest { inner })
    }
}

#[pyclass(
    name = "CancelOrderRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeCancelOrderRequest {
    inner: CancelOrderRequest,
}
#[pymethods]
impl NativeCancelOrderRequest {
    #[new]
    #[pyo3(signature = (*, reason=None))]
    fn new(reason: Option<String>) -> Self {
        Self {
            inner: CancelOrderRequest { reason },
        }
    }
}

#[pyclass(
    name = "ReplaceOrderRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeReplaceOrderRequest {
    inner: ReplaceOrderRequest,
}
#[pymethods]
impl NativeReplaceOrderRequest {
    #[new]
    #[pyo3(signature = (*, quantity=None, limit_price=None, options=None, reason=None))]
    fn new(
        quantity: Option<String>,
        limit_price: Option<String>,
        options: Option<PyRef<'_, NativeExecutionOrderOptionsRequest>>,
        reason: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ReplaceOrderRequest {
                quantity: quantity.map(|v| parse_quantity(&v)).transpose()?,
                limit_price: limit_price.map(|v| parse_price(&v)).transpose()?,
                options: options.map(|v| v.inner.clone()),
                reason,
            },
        })
    }
}

#[pyclass(
    name = "ReconcileExecutionRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeReconcileExecutionRequest {
    inner: ReconcileExecutionRequest,
}
#[pymethods]
impl NativeReconcileExecutionRequest {
    #[new]
    #[pyo3(signature = (*, account_id=None, execution_route_id=None, order_id=None, reason=None))]
    fn new(
        account_id: Option<String>,
        execution_route_id: Option<String>,
        order_id: Option<String>,
        reason: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ReconcileExecutionRequest {
                account_id: account_id
                    .map(AccountId::new)
                    .transpose()
                    .map_err(value_error)?,
                execution_route_id: execution_route_id
                    .map(ExecutionRouteId::new)
                    .transpose()
                    .map_err(value_error)?,
                order_id: order_id
                    .map(OrderId::new)
                    .transpose()
                    .map_err(value_error)?,
                reason,
            },
        })
    }
}

#[pyclass(
    name = "AdvanceExecutionTimeRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeAdvanceExecutionTimeRequest {
    inner: AdvanceExecutionTimeRequest,
}
#[pymethods]
impl NativeAdvanceExecutionTimeRequest {
    #[new]
    fn new(event_time_unix_nanos: u64) -> Self {
        Self {
            inner: AdvanceExecutionTimeRequest {
                event_time_unix_nanos: UnixNanos::new(event_time_unix_nanos),
            },
        }
    }
}

#[pyclass(
    name = "ExecutionRoutesQuery",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionRoutesQuery {
    inner: ExecutionRoutesQuery,
}
#[pymethods]
impl NativeExecutionRoutesQuery {
    #[new]
    #[pyo3(signature = (*, account_id=None, segment_key=None, instrument_id=None, market_id=None, broker_id=None))]
    fn new(
        account_id: Option<String>,
        segment_key: Option<String>,
        instrument_id: Option<String>,
        market_id: Option<String>,
        broker_id: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ExecutionRoutesQuery {
                account_id: account_id
                    .map(AccountId::new)
                    .transpose()
                    .map_err(value_error)?,
                segment_key: segment_key
                    .map(SegmentKey::new)
                    .transpose()
                    .map_err(value_error)?,
                instrument_id: instrument_id
                    .map(InstrumentId::new)
                    .transpose()
                    .map_err(value_error)?,
                market_id: market_id
                    .map(MarketId::new)
                    .transpose()
                    .map_err(value_error)?,
                broker_id: broker_id
                    .map(BrokerId::new)
                    .transpose()
                    .map_err(value_error)?,
            },
        })
    }
}

#[pyclass(
    name = "ExecutionOrderAuditQuery",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionOrderAuditQuery {
    inner: ExecutionOrderAuditQuery,
}

#[pyclass(
    name = "ExecutionRouteCandidate",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionRouteCandidate {
    #[pyo3(get)]
    route_id: String,
    #[pyo3(get)]
    account_id: Option<String>,
    #[pyo3(get)]
    segment_key: Option<String>,
    #[pyo3(get)]
    instrument_id: Option<String>,
    #[pyo3(get)]
    market_id: Option<String>,
    #[pyo3(get)]
    broker_id: String,
    #[pyo3(get)]
    execution_channel: String,
    #[pyo3(get)]
    order_entry_symbol: String,
    #[pyo3(get)]
    supported_order_types: Vec<String>,
    #[pyo3(get)]
    supported_options: Vec<String>,
    #[pyo3(get)]
    ready: bool,
}

#[pyclass(
    name = "ExecutionRoutesResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionRoutesResponse {
    #[pyo3(get)]
    routes: Vec<Py<NativeExecutionRouteCandidate>>,
}

#[pyclass(
    name = "ExecutionAttemptEvidence",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionAttemptEvidence {
    #[pyo3(get)]
    attempt_id: String,
    #[pyo3(get)]
    command: String,
    #[pyo3(get)]
    route_id: String,
    #[pyo3(get)]
    broker_id: String,
    #[pyo3(get)]
    execution_channel: String,
    #[pyo3(get)]
    order_entry_symbol: String,
    #[pyo3(get)]
    destination_market_id: Option<String>,
    #[pyo3(get)]
    route_selected_at_unix_nanos: u64,
    #[pyo3(get)]
    route_selection: String,
    #[pyo3(get)]
    provider_connection_id: String,
    #[pyo3(get)]
    command_started_at_unix_nanos: u64,
    #[pyo3(get)]
    delivery_certainty: String,
    #[pyo3(get)]
    remote_order_id: Option<String>,
}

#[pyclass(
    name = "ExecutionOrderAuditEvent",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionOrderAuditEvent {
    #[pyo3(get)]
    sequence: u64,
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    lifecycle: String,
    #[pyo3(get)]
    remote_order_id: Option<String>,
    #[pyo3(get)]
    occurred_at_unix_nanos: u64,
    #[pyo3(get)]
    reason: String,
    #[pyo3(get)]
    attempt: Option<Py<NativeExecutionAttemptEvidence>>,
}

#[pyclass(
    name = "ExecutionOrderAuditResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionOrderAuditResponse {
    #[pyo3(get)]
    events: Vec<Py<NativeExecutionOrderAuditEvent>>,
}
#[pymethods]
impl NativeExecutionOrderAuditQuery {
    #[new]
    #[pyo3(signature = (*, order_id=None, remote_order_id=None, lifecycle=None, since_unix_nanos=None, until_unix_nanos=None, limit=1000))]
    fn new(
        order_id: Option<String>,
        remote_order_id: Option<String>,
        lifecycle: Option<&str>,
        since_unix_nanos: Option<u64>,
        until_unix_nanos: Option<u64>,
        limit: u32,
    ) -> PyResult<Self> {
        if limit == 0 {
            return Err(ExecutionInvalidInputError::new_err(
                "audit limit must be positive",
            ));
        }
        Ok(Self {
            inner: ExecutionOrderAuditQuery {
                order_id: order_id
                    .map(OrderId::new)
                    .transpose()
                    .map_err(value_error)?,
                remote_order_id: remote_order_id
                    .map(RemoteOrderId::new)
                    .transpose()
                    .map_err(value_error)?,
                lifecycle: lifecycle.map(parse_order_lifecycle).transpose()?,
                since_unix_nanos: since_unix_nanos.map(UnixNanos::new),
                until_unix_nanos: until_unix_nanos.map(UnixNanos::new),
                limit,
            },
        })
    }
}

#[pyclass(
    name = "ExecutionCommandStatus",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionCommandStatus {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    command_id: Option<String>,
    #[pyo3(get)]
    intent_id: Option<String>,
    #[pyo3(get)]
    order_id: Option<String>,
}

#[pyclass(
    name = "ExecutionHealth",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionHealth {
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    writer_recovery_ready: bool,
    #[pyo3(get)]
    risk_recovery_ready: bool,
    #[pyo3(get)]
    risk_recovery_error: Option<String>,
    #[pyo3(get)]
    reconciliation_required_orders: u64,
    #[pyo3(get)]
    reconciliation_required_intents: u64,
    #[pyo3(get)]
    unresolved_remote_orders: u64,
    #[pyo3(get)]
    indeterminate_algorithm_actions: u64,
    #[pyo3(get)]
    outbox_backlog: u64,
}

#[pyclass(
    name = "ExecutionReconcileResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionReconcileResponse {
    #[pyo3(get)]
    changed: u64,
}

#[pyclass(
    name = "AdvanceExecutionTimeResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeAdvanceExecutionTimeResponse {
    #[pyo3(get)]
    advanced_to_unix_nanos: u64,
}

#[pyclass(
    name = "ExecutionBacktestEquityPoint",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestEquityPoint {
    inner: ExecutionBacktestEquityPoint,
}
#[pymethods]
impl NativeExecutionBacktestEquityPoint {
    #[new]
    fn new(observed_at_unix_nanos: u64, equity: String) -> PyResult<Self> {
        Ok(Self {
            inner: ExecutionBacktestEquityPoint {
                observed_at_unix_nanos: UnixNanos::new(observed_at_unix_nanos),
                equity: parse_money(&equity)?,
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestInputFill",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestInputFill {
    inner: ExecutionBacktestFill,
}
#[pymethods]
impl NativeExecutionBacktestInputFill {
    #[new]
    #[pyo3(signature = (*, instrument_id, side, quantity, price, occurred_at_unix_nanos, fee="0"))]
    fn new(
        instrument_id: String,
        side: String,
        quantity: String,
        price: String,
        occurred_at_unix_nanos: u64,
        fee: &str,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ExecutionBacktestFill {
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                side: side.parse().map_err(value_error)?,
                quantity: parse_quantity(&quantity)?,
                price: parse_price(&price)?,
                fee: parse_money(fee)?,
                occurred_at_unix_nanos: UnixNanos::new(occurred_at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestOrderRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestOrderRequest {
    inner: ExecutionBacktestOrderRequest,
}
#[pymethods]
impl NativeExecutionBacktestOrderRequest {
    #[new]
    #[pyo3(signature = (*, order_id, instrument_id, side, order_type, quantity, submitted_at_unix_nanos, market_id=None, limit_price=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        order_id: String,
        instrument_id: String,
        side: String,
        order_type: &str,
        quantity: String,
        submitted_at_unix_nanos: u64,
        market_id: Option<String>,
        limit_price: Option<String>,
    ) -> PyResult<Self> {
        let order_type = match order_type.to_ascii_lowercase().as_str() {
            "market" => OrderType::Market,
            "limit" => OrderType::Limit,
            _ => {
                return Err(ExecutionInvalidInputError::new_err(
                    "unsupported backtest order type",
                ));
            },
        };
        Ok(Self {
            inner: ExecutionBacktestOrderRequest {
                order_id: OrderId::new(order_id).map_err(value_error)?,
                instrument_id: InstrumentId::new(instrument_id).map_err(value_error)?,
                market_id: market_id
                    .map(MarketId::new)
                    .transpose()
                    .map_err(value_error)?,
                side: side.parse().map_err(value_error)?,
                order_type,
                quantity: parse_quantity(&quantity)?,
                limit_price: limit_price.map(|v| parse_price(&v)).transpose()?,
                submitted_at_unix_nanos: UnixNanos::new(submitted_at_unix_nanos),
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestSimulationConfig",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestSimulationConfig {
    inner: ExecutionBacktestSimulationConfig,
}
#[pymethods]
impl NativeExecutionBacktestSimulationConfig {
    #[new]
    #[pyo3(signature = (*, fee_bps="0", fee_currency=None, slippage_bps="0", enforce_quote_quantity=true))]
    fn new(
        fee_bps: &str,
        fee_currency: Option<String>,
        slippage_bps: &str,
        enforce_quote_quantity: bool,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: ExecutionBacktestSimulationConfig {
                fee_bps: parse_rate(fee_bps)?,
                fee_currency: fee_currency
                    .map(Currency::new)
                    .transpose()
                    .map_err(value_error)?,
                slippage_bps: parse_rate(slippage_bps)?,
                enforce_quote_quantity,
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestRequest {
    inner: ExecutionBacktestRequest,
}
#[pymethods]
impl NativeExecutionBacktestRequest {
    #[new]
    #[pyo3(signature = (*, initial_equity, equity_curve=Vec::new(), fills=Vec::new(), risk_free_rate="0", annualization_periods=None, market_events=Vec::new(), orders=Vec::new(), simulation=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        initial_equity: String,
        equity_curve: Vec<Py<NativeExecutionBacktestEquityPoint>>,
        fills: Vec<Py<NativeExecutionBacktestInputFill>>,
        risk_free_rate: &str,
        annualization_periods: Option<f64>,
        market_events: Vec<Py<NativeExecutionBacktestMarketRequest>>,
        orders: Vec<Py<NativeExecutionBacktestOrderRequest>>,
        simulation: Option<Py<NativeExecutionBacktestSimulationConfig>>,
    ) -> PyResult<Self> {
        if annualization_periods.is_some_and(|value| !value.is_finite() || value <= 0.0) {
            return Err(ExecutionInvalidInputError::new_err(
                "annualization_periods must be finite and positive",
            ));
        }
        Ok(Self {
            inner: ExecutionBacktestRequest {
                initial_equity: parse_money(&initial_equity)?,
                equity_curve: equity_curve
                    .into_iter()
                    .map(|v| v.borrow(py).inner.clone())
                    .collect(),
                fills: fills
                    .into_iter()
                    .map(|v| v.borrow(py).inner.clone())
                    .collect(),
                risk_free_rate: parse_rate(risk_free_rate)?,
                annualization_periods,
                market_events: market_events
                    .into_iter()
                    .map(|v| v.borrow(py).inner.event.clone())
                    .collect(),
                orders: orders
                    .into_iter()
                    .map(|v| v.borrow(py).inner.clone())
                    .collect(),
                simulation: simulation
                    .map(|v| v.borrow(py).inner.clone())
                    .unwrap_or_default(),
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestMetrics",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionBacktestMetrics {
    #[pyo3(get)]
    trade_count: usize,
    #[pyo3(get)]
    win_count: usize,
    #[pyo3(get)]
    loss_count: usize,
    #[pyo3(get)]
    win_rate: String,
    #[pyo3(get)]
    gross_profit: String,
    #[pyo3(get)]
    gross_loss: String,
    #[pyo3(get)]
    net_profit: String,
    #[pyo3(get)]
    max_drawdown: String,
    #[pyo3(get)]
    max_drawdown_pct: String,
    #[pyo3(get)]
    sharpe: String,
}

#[pyclass(
    name = "ExecutionBacktestOrder",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionBacktestOrder {
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    status: String,
    #[pyo3(get)]
    filled_quantity: String,
    #[pyo3(get)]
    remaining_quantity: String,
    #[pyo3(get)]
    updated_at_unix_nanos: u64,
    #[pyo3(get)]
    reason: String,
}

#[pyclass(
    name = "ExecutionBacktestRunResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionBacktestRunResponse {
    #[pyo3(get)]
    metrics: Py<NativeExecutionBacktestMetrics>,
    #[pyo3(get)]
    orders: Vec<Py<NativeExecutionBacktestOrder>>,
    #[pyo3(get)]
    fills: Vec<Py<NativeExecutionBacktestFill>>,
}

#[pyclass(
    name = "ExecutionBacktestMarketRequest",
    frozen,
    module = "kairospy._native_execution_contract"
)]
#[derive(Clone)]
pub(crate) struct NativeExecutionBacktestMarketRequest {
    inner: ExecutionBacktestMarketRequest,
}

#[pymethods]
impl NativeExecutionBacktestMarketRequest {
    #[staticmethod]
    #[pyo3(signature = (*, market_id, instrument_id, observed_at_unix_nanos, source_id, bid_price=None, bid_quantity=None, ask_price=None, ask_quantity=None))]
    #[allow(clippy::too_many_arguments)]
    fn quote(
        market_id: String,
        instrument_id: String,
        observed_at_unix_nanos: u64,
        source_id: String,
        bid_price: Option<String>,
        bid_quantity: Option<String>,
        ask_price: Option<String>,
        ask_quantity: Option<String>,
    ) -> PyResult<Self> {
        validate_text(&market_id, "market_id")?;
        validate_text(&instrument_id, "instrument_id")?;
        validate_text(&source_id, "source_id")?;
        for (name, value) in [
            ("bid_price", bid_price.as_deref()),
            ("bid_quantity", bid_quantity.as_deref()),
            ("ask_price", ask_price.as_deref()),
            ("ask_quantity", ask_quantity.as_deref()),
        ] {
            if let Some(value) = value {
                decimal_parts(value).map_err(|error| {
                    ExecutionInvalidInputError::new_err(format!("{name}: {error}"))
                })?;
            }
        }
        Ok(Self {
            inner: ExecutionBacktestMarketRequest {
                event: ExecutionBacktestMarketObservation::Quote(ExecutionBacktestQuote {
                    scope: ExecutionBacktestObservationScope::Market { market_id },
                    instrument_id,
                    bid_price,
                    bid_quantity,
                    ask_price,
                    ask_quantity,
                    observed_at_unix_nanos,
                    source_id,
                }),
            },
        })
    }

    #[staticmethod]
    #[pyo3(signature = (*, market_id, instrument_id, timeframe, open, high, low, close, observed_at_unix_nanos, source_id, volume=None, derivation="provider"))]
    #[allow(clippy::too_many_arguments)]
    fn bar(
        market_id: String,
        instrument_id: String,
        timeframe: String,
        open: String,
        high: String,
        low: String,
        close: String,
        observed_at_unix_nanos: u64,
        source_id: String,
        volume: Option<String>,
        derivation: &str,
    ) -> PyResult<Self> {
        for (name, value) in [
            ("market_id", &market_id),
            ("instrument_id", &instrument_id),
            ("timeframe", &timeframe),
            ("source_id", &source_id),
        ] {
            validate_text(value, name)?;
        }
        validate_text(derivation, "derivation")?;
        for (name, value) in [
            ("open", &open),
            ("high", &high),
            ("low", &low),
            ("close", &close),
        ] {
            decimal_parts(value)
                .map_err(|error| ExecutionInvalidInputError::new_err(format!("{name}: {error}")))?;
        }
        if let Some(value) = &volume {
            decimal_parts(value)?;
        }
        Ok(Self {
            inner: ExecutionBacktestMarketRequest {
                event: ExecutionBacktestMarketObservation::Bar(ExecutionBacktestBar {
                    scope: ExecutionBacktestObservationScope::Market { market_id },
                    instrument_id,
                    timeframe,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    observed_at_unix_nanos,
                    source_id,
                    derivation: derivation.to_owned(),
                }),
            },
        })
    }
}

#[pyclass(
    name = "ExecutionBacktestFill",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionBacktestFill {
    #[pyo3(get)]
    fill_id: String,
    #[pyo3(get)]
    order_id: String,
    #[pyo3(get)]
    instrument_id: String,
    #[pyo3(get)]
    execution_market_id: Option<String>,
    #[pyo3(get)]
    side: String,
    #[pyo3(get)]
    quantity: String,
    #[pyo3(get)]
    price: String,
    #[pyo3(get)]
    fee: String,
    #[pyo3(get)]
    fee_currency: Option<String>,
    #[pyo3(get)]
    occurred_at_unix_nanos: u64,
}

#[pyclass(
    name = "ExecutionBacktestMarketResponse",
    frozen,
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionBacktestMarketResponse {
    #[pyo3(get)]
    fills: Vec<Py<NativeExecutionBacktestFill>>,
}

#[pyclass(
    name = "ExecutionControlClient",
    module = "kairospy._native_execution_contract"
)]
pub(crate) struct NativeExecutionControlClient {
    client: kairos_protocol::ContractClient,
    timeout: Duration,
}

#[pymethods]
impl NativeExecutionControlClient {
    #[getter]
    fn socket_path(&self) -> PathBuf {
        self.client.control_socket_path().to_path_buf()
    }

    #[new]
    #[pyo3(signature = (socket_path, *, timeout=5.0))]
    fn new(socket_path: PathBuf, timeout: f64) -> PyResult<Self> {
        if !timeout.is_finite() || timeout <= 0.0 {
            return Err(ExecutionInvalidInputError::new_err(
                "Execution control timeout must be finite and positive",
            ));
        }
        Ok(Self {
            client: kairos_protocol::ContractClient::control_only(socket_path),
            timeout: Duration::from_secs_f64(timeout),
        })
    }

    fn health(&self, py: Python<'_>) -> PyResult<NativeExecutionHealth> {
        let client = self.client.clone();
        let value: ExecutionHealthResponse =
            py.detach(|| run(self.timeout, async move { client.control().health().await }))?;
        Ok(NativeExecutionHealth {
            status: value.status,
            writer_recovery_ready: value.writer_recovery_ready,
            risk_recovery_ready: value.risk_recovery_ready,
            risk_recovery_error: value.risk_recovery_error,
            reconciliation_required_orders: value.reconciliation_required_orders,
            reconciliation_required_intents: value.reconciliation_required_intents,
            unresolved_remote_orders: value.unresolved_remote_orders,
            indeterminate_algorithm_actions: value.indeterminate_algorithm_actions,
            outbox_backlog: value.outbox_backlog,
        })
    }

    fn routes(
        &self,
        py: Python<'_>,
        query: PyRef<'_, NativeExecutionRoutesQuery>,
    ) -> PyResult<NativeExecutionRoutesResponse> {
        let client = self.client.clone();
        let query = query.inner.clone();
        let value: ExecutionRoutesResponse = py.detach(|| {
            run(
                self.timeout,
                async move { client.control().routes(query).await },
            )
        })?;
        Ok(NativeExecutionRoutesResponse {
            routes: value
                .routes
                .into_iter()
                .map(|value| Py::new(py, route_candidate(value)))
                .collect::<PyResult<_>>()?,
        })
    }

    fn order_audit(
        &self,
        py: Python<'_>,
        query: PyRef<'_, NativeExecutionOrderAuditQuery>,
    ) -> PyResult<NativeExecutionOrderAuditResponse> {
        let client = self.client.clone();
        let query = query.inner.clone();
        let value: ExecutionOrderAuditResponse = py.detach(|| {
            run(self.timeout, async move {
                client.control().order_audit(query).await
            })
        })?;
        Ok(NativeExecutionOrderAuditResponse {
            events: value
                .events
                .into_iter()
                .map(|value| audit_event(py, value))
                .collect::<PyResult<_>>()?,
        })
    }

    fn submit_intent(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeSubmitIntentRequest>,
    ) -> PyResult<NativeExecutionCommandStatus> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let value = py.detach(|| {
            run(self.timeout, async move {
                client.control().submit_intent(request).await
            })
        })?;
        Ok(command_status(value))
    }

    fn cancel_order(
        &self,
        py: Python<'_>,
        order_id: String,
        request: PyRef<'_, NativeCancelOrderRequest>,
    ) -> PyResult<NativeExecutionCommandStatus> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let order_id = OrderId::new(order_id).map_err(value_error)?;
        let value = py.detach(|| {
            run(self.timeout, async move {
                client.control().cancel_order(order_id, request).await
            })
        })?;
        Ok(command_status(value))
    }

    fn replace_order(
        &self,
        py: Python<'_>,
        order_id: String,
        request: PyRef<'_, NativeReplaceOrderRequest>,
    ) -> PyResult<NativeExecutionCommandStatus> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let order_id = OrderId::new(order_id).map_err(value_error)?;
        let value = py.detach(|| {
            run(self.timeout, async move {
                client.control().replace_order(order_id, request).await
            })
        })?;
        Ok(command_status(value))
    }

    fn reconcile(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeReconcileExecutionRequest>,
    ) -> PyResult<NativeExecutionReconcileResponse> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let value: ExecutionReconcileResponse = py.detach(|| {
            run(self.timeout, async move {
                client.control().reconcile(request).await
            })
        })?;
        Ok(NativeExecutionReconcileResponse {
            changed: value.changed,
        })
    }

    fn advance_time(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeAdvanceExecutionTimeRequest>,
    ) -> PyResult<NativeAdvanceExecutionTimeResponse> {
        let client = self.client.clone();
        let request = request.inner;
        let value = py.detach(|| {
            run(self.timeout, async move {
                client.control().advance_time(request).await
            })
        })?;
        Ok(NativeAdvanceExecutionTimeResponse {
            advanced_to_unix_nanos: value.advanced_to_unix_nanos.get(),
        })
    }

    fn backtest_market(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeExecutionBacktestMarketRequest>,
    ) -> PyResult<NativeExecutionBacktestMarketResponse> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let value: ExecutionBacktestMarketResponse = py.detach(|| {
            run(self.timeout, async move {
                client.control().backtest_market(request).await
            })
        })?;
        Ok(NativeExecutionBacktestMarketResponse {
            fills: value
                .fills
                .into_iter()
                .map(|value| Py::new(py, backtest_fill(value)))
                .collect::<PyResult<_>>()?,
        })
    }

    fn backtest_run(
        &self,
        py: Python<'_>,
        request: PyRef<'_, NativeExecutionBacktestRequest>,
    ) -> PyResult<NativeExecutionBacktestRunResponse> {
        let client = self.client.clone();
        let request = request.inner.clone();
        let value: ExecutionBacktestRunResponse = py.detach(|| {
            run(self.timeout, async move {
                client.control().backtest_run(request).await
            })
        })?;
        project_backtest_run(py, value)
    }
}

fn command_status(value: ExecutionCommandStatus) -> NativeExecutionCommandStatus {
    NativeExecutionCommandStatus {
        status: value.status,
        command_id: value.command_id.map(|v| v.to_string()),
        intent_id: value.intent_id.map(|v| v.to_string()),
        order_id: value.order_id.map(|v| v.to_string()),
    }
}

fn route_candidate(value: ExecutionRouteCandidateResponse) -> NativeExecutionRouteCandidate {
    NativeExecutionRouteCandidate {
        route_id: value.route_id.to_string(),
        account_id: value.account_id.map(|v| v.to_string()),
        segment_key: value.segment_key.map(|v| v.to_string()),
        instrument_id: value.instrument_id.map(|v| v.to_string()),
        market_id: value.market_id.map(|v| v.to_string()),
        broker_id: value.broker_id.to_string(),
        execution_channel: value.execution_channel.to_string(),
        order_entry_symbol: value.order_entry_symbol.to_string(),
        supported_order_types: value
            .supported_order_types
            .into_iter()
            .map(|v| {
                match v {
                    kairos_primitives::execution::OrderType::Market => "market",
                    kairos_primitives::execution::OrderType::Limit => "limit",
                }
                .to_owned()
            })
            .collect(),
        supported_options: value
            .supported_options
            .into_iter()
            .map(|v| v.to_string())
            .collect(),
        ready: value.ready,
    }
}

fn audit_event(
    py: Python<'_>,
    value: ExecutionOrderAuditEventResponse,
) -> PyResult<Py<NativeExecutionOrderAuditEvent>> {
    Py::new(
        py,
        NativeExecutionOrderAuditEvent {
            sequence: value.sequence.get(),
            order_id: value.order_id.to_string(),
            lifecycle: order_lifecycle(value.lifecycle).to_owned(),
            remote_order_id: value.remote_order_id.map(|v| v.to_string()),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos.get(),
            reason: value.reason,
            attempt: value
                .attempt
                .map(|v| Py::new(py, attempt_evidence(v)))
                .transpose()?,
        },
    )
}

fn attempt_evidence(value: ExecutionAttemptEvidenceResponse) -> NativeExecutionAttemptEvidence {
    NativeExecutionAttemptEvidence {
        attempt_id: value.attempt_id,
        command: format!("{:?}", value.command).to_ascii_lowercase(),
        route_id: value.route_id.to_string(),
        broker_id: value.broker_id.to_string(),
        execution_channel: value.execution_channel.to_string(),
        order_entry_symbol: value.order_entry_symbol.to_string(),
        destination_market_id: value.destination_market_id.map(|v| v.to_string()),
        route_selected_at_unix_nanos: value.route_selected_at_unix_nanos.get(),
        route_selection: match value.route_selection {
            kairos_execution_contract::ExecutionRouteSelection::Explicit => "explicit",
            kairos_execution_contract::ExecutionRouteSelection::UniqueCandidate => {
                "unique_candidate"
            },
        }
        .to_owned(),
        provider_connection_id: value.provider_connection_id,
        command_started_at_unix_nanos: value.command_started_at_unix_nanos.get(),
        delivery_certainty: format!("{:?}", value.delivery_certainty).to_ascii_lowercase(),
        remote_order_id: value.remote_order_id.map(|v| v.to_string()),
    }
}

fn order_lifecycle(value: ExecutionOrderLifecycle) -> &'static str {
    match value {
        ExecutionOrderLifecycle::Pending => "pending",
        ExecutionOrderLifecycle::Submitting => "submitting",
        ExecutionOrderLifecycle::Accepted => "accepted",
        ExecutionOrderLifecycle::PartiallyFilled => "partially_filled",
        ExecutionOrderLifecycle::Filled => "filled",
        ExecutionOrderLifecycle::CancelRequested => "cancel_requested",
        ExecutionOrderLifecycle::Canceled => "canceled",
        ExecutionOrderLifecycle::Rejected => "rejected",
        ExecutionOrderLifecycle::Expired => "expired",
        ExecutionOrderLifecycle::Unknown => "unknown",
        ExecutionOrderLifecycle::Failed => "failed",
    }
}

fn backtest_fill(value: ExecutionBacktestSimulationFill) -> NativeExecutionBacktestFill {
    NativeExecutionBacktestFill {
        fill_id: value.fill_id.to_string(),
        order_id: value.order_id.to_string(),
        instrument_id: value.instrument_id.to_string(),
        execution_market_id: value.execution_market_id.map(|v| v.to_string()),
        side: match value.side {
            OrderSide::Buy => "buy",
            OrderSide::Sell => "sell",
        }
        .to_owned(),
        quantity: value.quantity.to_string(),
        price: value.price.to_string(),
        fee: value.fee.to_string(),
        fee_currency: value.fee_currency.map(|v| v.to_string()),
        occurred_at_unix_nanos: value.occurred_at_unix_nanos.get(),
    }
}

fn project_backtest_run(
    py: Python<'_>,
    value: ExecutionBacktestRunResponse,
) -> PyResult<NativeExecutionBacktestRunResponse> {
    Ok(NativeExecutionBacktestRunResponse {
        metrics: Py::new(py, backtest_metrics(value.metrics))?,
        orders: value
            .orders
            .into_iter()
            .map(|value| Py::new(py, backtest_order(value)))
            .collect::<PyResult<_>>()?,
        fills: value
            .fills
            .into_iter()
            .map(|value| Py::new(py, backtest_fill(value)))
            .collect::<PyResult<_>>()?,
    })
}

fn backtest_metrics(value: ExecutionBacktestMetrics) -> NativeExecutionBacktestMetrics {
    NativeExecutionBacktestMetrics {
        trade_count: value.trade_count,
        win_count: value.win_count,
        loss_count: value.loss_count,
        win_rate: value.win_rate,
        gross_profit: value.gross_profit,
        gross_loss: value.gross_loss,
        net_profit: value.net_profit,
        max_drawdown: value.max_drawdown,
        max_drawdown_pct: value.max_drawdown_pct,
        sharpe: value.sharpe,
    }
}

fn backtest_order(value: ExecutionBacktestOrder) -> NativeExecutionBacktestOrder {
    NativeExecutionBacktestOrder {
        order_id: value.request.order_id.to_string(),
        status: match value.status {
            ExecutionBacktestOrderStatus::Accepted => "accepted",
            ExecutionBacktestOrderStatus::PartiallyFilled => "partially_filled",
            ExecutionBacktestOrderStatus::Filled => "filled",
            ExecutionBacktestOrderStatus::Canceled => "canceled",
            ExecutionBacktestOrderStatus::Rejected => "rejected",
        }
        .to_owned(),
        filled_quantity: value.filled_quantity.to_string(),
        remaining_quantity: value.remaining_quantity.to_string(),
        updated_at_unix_nanos: value.updated_at_unix_nanos.get(),
        reason: value.reason,
    }
}

fn run<F, T, E>(timeout: Duration, future: F) -> PyResult<T>
where
    F: std::future::Future<Output = Result<T, E>>,
    E: kairos_protocol::contract::ControlCallError,
{
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_io()
        .enable_time()
        .build()
        .map_err(|e| ExecutionControlUnavailableError::new_err(e.to_string()))?;
    runtime
        .block_on(async move { tokio::time::timeout(timeout, future).await })
        .map_err(|_| {
            ExecutionControlUnavailableError::new_err("Execution control request timed out")
        })?
        .map_err(|error| {
            if kairos_protocol::contract::is_control_rejection(&error) {
                ExecutionControlRejectedError::new_err(error.to_string())
            } else {
                ExecutionControlUnavailableError::new_err(error.to_string())
            }
        })
}

fn decimal_parts(value: &str) -> PyResult<DecimalParts> {
    value.parse().map_err(value_error)
}
fn parse_quantity(value: &str) -> PyResult<Quantity> {
    let p = decimal_parts(value)?;
    Quantity::new(p.mantissa(), p.scale()).map_err(value_error)
}
fn parse_price(value: &str) -> PyResult<Price> {
    let p = decimal_parts(value)?;
    Price::new(p.mantissa(), p.scale()).map_err(value_error)
}
fn parse_money(value: &str) -> PyResult<Money> {
    let p = decimal_parts(value)?;
    Money::new(p.mantissa(), p.scale()).map_err(value_error)
}
fn parse_rate(value: &str) -> PyResult<Rate> {
    let p = decimal_parts(value)?;
    Rate::new(p.mantissa(), p.scale()).map_err(value_error)
}
fn parse_ratio(value: &str) -> PyResult<Ratio> {
    let p = decimal_parts(value)?;
    let numerator = u64::try_from(p.mantissa())
        .map_err(|_| ExecutionInvalidInputError::new_err("ratio must be non-negative"))?;
    let denominator = 10_u64
        .checked_pow(u32::from(p.scale()))
        .ok_or_else(|| ExecutionInvalidInputError::new_err("ratio scale is too large"))?;
    Ratio::new(numerator, denominator).map_err(value_error)
}
fn signed_quantity(value: &str) -> PyResult<SignedQuantity> {
    let p = decimal_parts(value)?;
    SignedQuantity::new(p.mantissa(), p.scale()).map_err(value_error)
}
fn value_error(error: impl std::fmt::Display) -> PyErr {
    ExecutionInvalidInputError::new_err(error.to_string())
}

fn validate_text(value: &str, name: &str) -> PyResult<()> {
    if value.trim().is_empty() {
        Err(ExecutionInvalidInputError::new_err(format!(
            "{name} is required"
        )))
    } else {
        Ok(())
    }
}

fn typed_hash(value: &ExecutionIntentRequest) -> PyResult<String> {
    let encoded = serde_json::to_vec(value).map_err(value_error)?;
    Ok(format!("{:x}", Sha256::digest(encoded)))
}

fn parse_intent_type(value: &str) -> PyResult<IntentType> {
    match normalize(value).as_str() {
        "singleorder" => Ok(IntentType::SingleOrder),
        "targetposition" => Ok(IntentType::TargetPosition),
        "pairarbitrage" => Ok(IntentType::PairArbitrage),
        "optionspread" => Ok(IntentType::OptionSpread),
        "portfoliorebalance" => Ok(IntentType::PortfolioRebalance),
        "quoteprovisioning" => Ok(IntentType::QuoteProvisioning),
        "hedge" => Ok(IntentType::Hedge),
        _ => Err(ExecutionInvalidInputError::new_err(
            "unsupported Execution intent type",
        )),
    }
}
fn intent_type(value: IntentType) -> &'static str {
    match value {
        IntentType::SingleOrder => "single_order",
        IntentType::TargetPosition => "target_position",
        IntentType::PairArbitrage => "pair_arbitrage",
        IntentType::OptionSpread => "option_spread",
        IntentType::PortfolioRebalance => "portfolio_rebalance",
        IntentType::QuoteProvisioning => "quote_provisioning",
        IntentType::Hedge => "hedge",
    }
}
fn completion_policy(value: CompletionPolicy) -> &'static str {
    match value {
        CompletionPolicy::AllLegsSatisfied => "all_legs_satisfied",
        CompletionPolicy::AllOrNothing => "all_or_nothing",
        CompletionPolicy::BestEffort => "best_effort",
        CompletionPolicy::HedgeWithinTolerance => "hedge_within_tolerance",
        CompletionPolicy::TargetQuantityReached => "target_quantity_reached",
    }
}
fn failure_policy(value: FailurePolicy) -> &'static str {
    match value {
        FailurePolicy::CancelRemaining => "cancel_remaining",
        FailurePolicy::ContinueOtherLegs => "continue_other_legs",
        FailurePolicy::Compensate => "compensate",
        FailurePolicy::PauseForManualIntervention => "pause_for_manual_intervention",
        FailurePolicy::MarkReconciliationRequired => "mark_reconciliation_required",
    }
}
fn parse_completion_policy(value: &str) -> PyResult<CompletionPolicy> {
    match normalize(value).as_str() {
        "alllegssatisfied" => Ok(CompletionPolicy::AllLegsSatisfied),
        "allornothing" => Ok(CompletionPolicy::AllOrNothing),
        "besteffort" => Ok(CompletionPolicy::BestEffort),
        "hedgewithintolerance" => Ok(CompletionPolicy::HedgeWithinTolerance),
        "targetquantityreached" => Ok(CompletionPolicy::TargetQuantityReached),
        _ => Err(ExecutionInvalidInputError::new_err(
            "unsupported Execution completion policy",
        )),
    }
}
fn parse_failure_policy(value: &str) -> PyResult<FailurePolicy> {
    match normalize(value).as_str() {
        "cancelremaining" => Ok(FailurePolicy::CancelRemaining),
        "continueotherlegs" => Ok(FailurePolicy::ContinueOtherLegs),
        "compensate" => Ok(FailurePolicy::Compensate),
        "pauseformanualintervention" => Ok(FailurePolicy::PauseForManualIntervention),
        "markreconciliationrequired" => Ok(FailurePolicy::MarkReconciliationRequired),
        _ => Err(ExecutionInvalidInputError::new_err(
            "unsupported Execution failure policy",
        )),
    }
}
fn parse_order_lifecycle(value: &str) -> PyResult<ExecutionOrderLifecycle> {
    match normalize(value).as_str() {
        "pending" => Ok(ExecutionOrderLifecycle::Pending),
        "submitting" => Ok(ExecutionOrderLifecycle::Submitting),
        "accepted" => Ok(ExecutionOrderLifecycle::Accepted),
        "partiallyfilled" => Ok(ExecutionOrderLifecycle::PartiallyFilled),
        "filled" => Ok(ExecutionOrderLifecycle::Filled),
        "cancelrequested" => Ok(ExecutionOrderLifecycle::CancelRequested),
        "canceled" => Ok(ExecutionOrderLifecycle::Canceled),
        "rejected" => Ok(ExecutionOrderLifecycle::Rejected),
        "expired" => Ok(ExecutionOrderLifecycle::Expired),
        "unknown" => Ok(ExecutionOrderLifecycle::Unknown),
        "failed" => Ok(ExecutionOrderLifecycle::Failed),
        _ => Err(ExecutionInvalidInputError::new_err(
            "unsupported Execution order lifecycle",
        )),
    }
}
fn normalize(value: &str) -> String {
    value
        .chars()
        .filter(|c| *c != '_' && *c != '-')
        .flat_map(char::to_lowercase)
        .collect()
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module
        .py()
        .get_type::<ExecutionControlUnavailableError>()
        .setattr("code", "transport_unavailable")?;
    module
        .py()
        .get_type::<ExecutionControlRejectedError>()
        .setattr("code", "operation_rejected")?;
    module.add(
        "ExecutionControlUnavailableError",
        module.py().get_type::<ExecutionControlUnavailableError>(),
    )?;
    module.add(
        "ExecutionControlRejectedError",
        module.py().get_type::<ExecutionControlRejectedError>(),
    )?;
    module.add_class::<NativeSplitOrderPolicyRequest>()?;
    module.add_class::<NativeMakerExecutionPolicyRequest>()?;
    module.add_class::<NativeExecutionOrderOptionsRequest>()?;
    module.add_class::<NativeIntentLegRequest>()?;
    module.add_class::<NativeExecutionBenchmarkRequest>()?;
    module.add_class::<NativeExecutionAlgorithmPolicyRequest>()?;
    module.add_class::<NativeExecutionIntentRequest>()?;
    module.add_class::<NativeIntentAdmissionEvidenceRequest>()?;
    module.add_class::<NativeSubmitIntentRequest>()?;
    module.add_class::<NativeCancelOrderRequest>()?;
    module.add_class::<NativeReplaceOrderRequest>()?;
    module.add_class::<NativeReconcileExecutionRequest>()?;
    module.add_class::<NativeAdvanceExecutionTimeRequest>()?;
    module.add_class::<NativeExecutionRoutesQuery>()?;
    module.add_class::<NativeExecutionOrderAuditQuery>()?;
    module.add_class::<NativeExecutionRouteCandidate>()?;
    module.add_class::<NativeExecutionRoutesResponse>()?;
    module.add_class::<NativeExecutionAttemptEvidence>()?;
    module.add_class::<NativeExecutionOrderAuditEvent>()?;
    module.add_class::<NativeExecutionOrderAuditResponse>()?;
    module.add_class::<NativeExecutionCommandStatus>()?;
    module.add_class::<NativeExecutionHealth>()?;
    module.add_class::<NativeExecutionReconcileResponse>()?;
    module.add_class::<NativeAdvanceExecutionTimeResponse>()?;
    module.add_class::<NativeExecutionBacktestEquityPoint>()?;
    module.add_class::<NativeExecutionBacktestInputFill>()?;
    module.add_class::<NativeExecutionBacktestOrderRequest>()?;
    module.add_class::<NativeExecutionBacktestSimulationConfig>()?;
    module.add_class::<NativeExecutionBacktestRequest>()?;
    module.add_class::<NativeExecutionBacktestMetrics>()?;
    module.add_class::<NativeExecutionBacktestOrder>()?;
    module.add_class::<NativeExecutionBacktestRunResponse>()?;
    module.add_class::<NativeExecutionBacktestMarketRequest>()?;
    module.add_class::<NativeExecutionBacktestFill>()?;
    module.add_class::<NativeExecutionBacktestMarketResponse>()?;
    module.add_class::<NativeExecutionControlClient>()?;
    Ok(())
}
