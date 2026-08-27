//! v2 public cross-process contract for Execution.

extern crate self as kairos_execution_contract;

use std::path::Path;

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod view;

pub use control::{
    AdvanceExecutionTimeRequest, AdvanceExecutionTimeResponse, CancelOrderRequest, CommandEnvelope,
    CompletionPolicy, ExecutionAlgorithmPolicyRequest, ExecutionAttemptCommand,
    ExecutionAttemptEvidenceResponse, ExecutionBacktestBar, ExecutionBacktestEquityPoint,
    ExecutionBacktestFill, ExecutionBacktestMarketObservation, ExecutionBacktestMarketRequest,
    ExecutionBacktestMarketResponse, ExecutionBacktestMetrics, ExecutionBacktestObservationScope,
    ExecutionBacktestOrder, ExecutionBacktestOrderRequest, ExecutionBacktestOrderStatus,
    ExecutionBacktestQuote, ExecutionBacktestQuoteBar, ExecutionBacktestRequest,
    ExecutionBacktestRunResponse, ExecutionBacktestSimulationConfig,
    ExecutionBacktestSimulationFill, ExecutionBacktestTradeBar, ExecutionBenchmarkKind,
    ExecutionBenchmarkRequest, ExecutionCommandStatus, ExecutionControlError,
    ExecutionControlResponse, ExecutionControlRpcClient, ExecutionControlRpcServer,
    ExecutionDeliveryCertainty, ExecutionHealthResponse, ExecutionIntentRequest,
    ExecutionOrderAuditEventResponse, ExecutionOrderAuditQuery, ExecutionOrderAuditResponse,
    ExecutionOrderLifecycle, ExecutionOrderOptionsRequest, ExecutionReconcileResponse,
    ExecutionRouteCandidateResponse, ExecutionRouteHealth, ExecutionRouteSelection,
    ExecutionRoutesQuery, ExecutionRoutesResponse, FailurePolicy, HedgePolicyRequest,
    IntentAdmissionEvidenceRequest, IntentLegRequest, IntentType, MakerExecutionPolicyRequest,
    PassiveLimitPolicyRequest, ReconcileExecutionRequest, ReplaceOrderRequest,
    SplitOrderPolicyRequest, SubmitIntentRequest, TwapPolicyRequest,
};
pub use encode::{EncodeContext, event_metadata};
pub use error::{ContractError, ContractResult};
pub use event::{
    ExecutionEvent, ExecutionEventFrame, ExecutionEventPublisher, ExecutionEventStream,
};
pub type ExecutionConnection = kairos_protocol::ContractClient;
pub const EXECUTION_EVENTS_STREAM_ID: i32 = kairos_transport::stream_ids::EXECUTION_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub const CONTRACT_FINGERPRINT: &str = "kairos.execution.contract.v2";

pub use kairos_transport::AeronEndpoint;
pub use view::{
    ALGORITHM_RUNS_DATABASE, COMMITMENTS_DATABASE, EXECUTION_MAP_SIZE, ExecutionIndexedView,
    ExecutionIndexedViewValue, INTENTS_DATABASE, ORDERS_DATABASE, RISK_RESERVATIONS_DATABASE,
    UNKNOWN_REMOTE_ORDERS_DATABASE, execution_indexed_environment_path, execution_indexed_identity,
    execution_indexed_schema_set, indexed_entity_key,
};

#[derive(Clone)]
pub struct ExecutionClient {
    inner: kairos_protocol::ContractClient,
}

impl ExecutionClient {
    pub fn connect(connection: ExecutionConnection) -> Self {
        Self { inner: connection }
    }

    pub fn control(&self) -> impl ExecutionControlRpcClient + '_ {
        self.inner.control()
    }

    pub fn events(&self, capacity: usize) -> ContractResult<ExecutionEventStream> {
        ExecutionEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }

    pub fn indexed_current(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
    ) -> ContractResult<view::ExecutionIndexedView> {
        view::ExecutionIndexedView::open(self.require_view_root()?, identity)
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
