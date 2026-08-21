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
pub use encode::{
    FileRiskSnapshotPublisher, FlatbuffersRiskEventWriter, FlatbuffersRiskSnapshotWriter,
    MmapRiskSnapshotPublisher, RiskAeronEventPublisher, RiskSnapshotPublisher,
};
pub use error::{ContractError, ContractResult};
pub use event::{DecodedRiskEvent, RiskEventFrame, RiskEventStream};
pub type RiskConnection = kairos_protocol::ContractClient;

pub use kairos_transport::AeronEndpoint;
use kairos_transport::SnapshotEnvelopeMetadata;
pub use view::{
    RiskViewKey, RiskViewKind, RiskViewPublisher, ViewFrame, ViewMetadata, risk_view_path,
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
    pub fn latest(&self, actor_id: impl Into<String>) -> ContractResult<RiskLatest> {
        RiskLatest::open(self.require_view_root()?, RiskViewKey::latest(actor_id))
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct RiskLatest {
    reader: view::RiskViewReader,
}

impl RiskLatest {
    fn open(root: &Path, key: RiskViewKey) -> ContractResult<Self> {
        Ok(Self {
            reader: view::RiskViewReader::open(root, key)?,
        })
    }

    pub fn read(&self) -> ContractResult<RiskLatestSnapshot> {
        Ok(RiskLatestSnapshot {
            frame: self.reader.read()?,
        })
    }

    pub fn key(&self) -> &RiskViewKey {
        self.reader.key()
    }
}

pub struct RiskLatestSnapshot {
    frame: ViewFrame,
}

impl RiskLatestSnapshot {
    pub fn generation(&self) -> u64 {
        self.frame.generation()
    }

    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.frame.envelope_metadata()
    }

    pub fn view(
        &self,
    ) -> ContractResult<kairos_protocol::generated::kairos::risk::v_2::RiskLatestView<'_>> {
        self.frame.decode()
    }
}
