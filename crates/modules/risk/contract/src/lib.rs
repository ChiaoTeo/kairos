//! v2 public cross-process contract for Risk.
//!
//! The crate owns the control, event, transport and view boundaries. It does
//! not expose the Risk service actor or its persistence representation.

extern crate self as kairos_risk_contract;

use std::path::Path;

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod view;

pub use control::{
    AdvanceRiskTimeRequest, AdvanceRiskTimeResponse, Allocation, Amount, AuthorizeRequest,
    CircuitScope, CircuitState, CloseCircuitRequest, ConsumeReservationRequest, DecimalValue,
    DependencyWatermarks, EnforcementMode, FundingRequirement, Health, LimitView, Metric,
    OpenCircuitRequest, PolicyScope, PublishPolicyRequest, ReasonCode, ReleaseReservationRequest,
    Reservation, ReservationStatus, ResizeReservationRequest, RiskCommandStatus, RiskContext,
    RiskControlError, RiskControlRpcClient, RiskControlRpcServer, RiskCurrentView, RiskDecision,
    RiskEvent, RiskPolicy, TradeRiskProposal,
};
pub use encode::{FlatbuffersRiskEventWriter, RiskAeronEventPublisher, encode_indexed_current};
pub use error::{ContractError, ContractResult};
pub use event::{DecodedRiskEvent, RiskEventFrame, RiskEventStream, decode_event};
pub type RiskConnection = kairos_protocol::ContractClient;
pub const RISK_EVENTS_STREAM_ID: i32 = kairos_transport::stream_ids::RISK_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub const CONTRACT_FINGERPRINT: &str = "kairos.risk.contract.v2";

pub use kairos_transport::AeronEndpoint;
pub use view::{
    RISK_ALLOCATIONS_DATABASE, RISK_CIRCUITS_DATABASE, RISK_LIMIT_USAGE_DATABASE, RISK_MAP_SIZE,
    RISK_POLICIES_DATABASE, RISK_RESERVATIONS_DATABASE, RISK_RESOURCE_EPOCH, RISK_STATE_DATABASE,
    RiskIndexedSnapshot, RiskIndexedView, RiskIndexedViewValue, RiskIndexedViewValueRef,
    risk_indexed_environment_path, risk_indexed_identity, risk_indexed_key,
    risk_indexed_schema_set,
};

#[derive(Clone)]
pub struct RiskClient {
    inner: kairos_protocol::ContractClient,
}

impl RiskClient {
    pub fn connect(connection: RiskConnection) -> ContractResult<Self> {
        Ok(Self { inner: connection })
    }
    pub fn control(&self) -> impl RiskControlRpcClient + '_ {
        self.inner.control()
    }
    pub fn events(&self, capacity: usize) -> ContractResult<RiskEventStream> {
        RiskEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }
    pub fn indexed_current(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
        actor_id: kairos_primitives::runtime::ActorId,
    ) -> ContractResult<RiskIndexedView> {
        RiskIndexedView::open(self.require_view_root()?, identity, actor_id)
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
