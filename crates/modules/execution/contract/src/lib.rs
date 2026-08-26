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
pub use encode::{EncodeContext, event_metadata, view_metadata};
pub use error::{ContractError, ContractResult};
pub use event::{
    ExecutionEvent, ExecutionEventFrame, ExecutionEventPublisher, ExecutionEventStream,
};
pub type ExecutionConnection = kairos_protocol::ContractClient;

pub use kairos_transport::AeronEndpoint;
use kairos_transport::SnapshotEnvelopeMetadata;
pub use view::{
    ExecutionViewKey, ExecutionViewKind, ExecutionViewPublisher, ViewFrame, ViewMetadata,
    execution_view_path,
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

    pub fn current_execution(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
    ) -> ContractResult<CurrentExecution> {
        CurrentExecution::open(
            self.require_view_root()?,
            ExecutionViewKey::from_identity(identity, ExecutionViewKind::CurrentExecution),
        )
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

macro_rules! execution_view_handle {
    ($handle:ident, $snapshot:ident, $view:ty, $decode:ident) => {
        pub struct $handle {
            reader: view::ExecutionViewReader,
        }

        impl $handle {
            fn open(root: &Path, key: ExecutionViewKey) -> ContractResult<Self> {
                Ok(Self {
                    reader: view::ExecutionViewReader::open(root, key)?,
                })
            }

            pub fn read(&self) -> ContractResult<$snapshot> {
                Ok($snapshot {
                    frame: self.reader.read()?,
                })
            }

            pub fn key(&self) -> &ExecutionViewKey {
                self.reader.key()
            }
        }

        pub struct $snapshot {
            frame: ViewFrame,
        }

        impl $snapshot {
            pub fn generation(&self) -> u64 {
                self.frame.generation()
            }

            pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
                self.frame.envelope_metadata()
            }

            pub fn view(&self) -> ContractResult<$view> {
                self.frame.$decode()
            }
        }
    };
}

execution_view_handle!(
    CurrentExecution,
    CurrentExecutionSnapshot,
    view::CurrentExecutionView<'_>,
    current_execution
);
