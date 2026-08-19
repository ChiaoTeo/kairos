//! v2 public cross-process contract for Risk.
//!
//! The crate owns the control, event, transport and view boundaries. It does
//! not expose the Risk service actor or its persistence representation.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;
pub use control::{
    AdvanceRiskTimeRequest, AdvanceRiskTimeResponse, Allocation, Amount, AuthorizeRequest,
    CircuitScope, CircuitState, CloseCircuitRequest, ConsumeReservationRequest, DecimalValue,
    DependencyWatermarks, EnforcementMode, FundingRequirement, LimitView, Metric,
    OpenCircuitRequest, PolicyScope, PublishPolicyRequest, ReasonCode, ReleaseReservationRequest,
    Reservation, ReservationStatus, ResizeReservationRequest, RiskCommandStatus, RiskContext,
    RiskControlError, RiskCurrentView, RiskDecision, RiskEvent, RiskPolicy, RiskRestRequest,
    RiskRestResponse, TradeRiskProposal,
};
pub use control::{Health, RiskControlClient};
pub use encode::{
    FlatbuffersRiskEventWriter, FlatbuffersRiskSnapshotWriter, MmapRiskSnapshotPublisher,
    RiskAeronEventPublisher,
};
pub use error::{ContractError, ContractResult};
pub use event::{DecodedRiskEvent, RiskEventFrame, RiskEventStream};
pub use kairos_transport::AeronEndpoint;
pub use view::{
    risk_view_path, RiskViewKey, RiskViewKind, RiskViewPublisher, RiskViewReader, ViewFrame,
    ViewMetadata,
};

use std::path::PathBuf;

pub struct RiskEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub events: AeronEndpoint,
}
pub struct RiskClient {
    control: RiskControlClient,
    view_root: PathBuf,
    events: AeronEndpoint,
}

impl RiskClient {
    pub fn connect(endpoint: RiskEndpoint) -> ContractResult<Self> {
        Ok(Self {
            control: RiskControlClient::connect(endpoint.control_socket)?,
            view_root: endpoint.view_root,
            events: endpoint.events,
        })
    }
    pub fn control(&self) -> &RiskControlClient {
        &self.control
    }
    pub fn events(&self, capacity: usize) -> ContractResult<RiskEventStream> {
        RiskEventStream::connect(&self.events, capacity)
    }
    pub fn view(&self, key: RiskViewKey) -> ContractResult<RiskViewReader> {
        RiskViewReader::open(&self.view_root, key)
    }
}
