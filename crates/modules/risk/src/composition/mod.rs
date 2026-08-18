use std::path::PathBuf;
use std::time::Duration;

use kairos_conflux::ConfluxSystem;

use crate::domain::RiskPolicy;
use crate::services::actor::RiskActor;
use crate::RiskApplication;

const RISK_VIEW_RESOURCE: &str = "risk-latest";
const RISK_EVENT_RESOURCE: &str = "risk-events";

pub struct RiskHostConfig {
    pub actor_id: String,
    pub policies: Vec<RiskPolicy>,
    pub state_path: Option<PathBuf>,
    pub socket_path: PathBuf,
    pub health_file: Option<PathBuf>,
    pub interval: Duration,
    pub replay_clock: bool,
    pub snapshot_path: PathBuf,
    pub snapshot_slot_size: usize,
    pub aeron_dir: Option<String>,
    pub event_channel: String,
    pub event_stream_id: i32,
    pub identity: kairos_protocol::InstanceIdentity,
}

/// Assemble the Risk Actor/Contract and its concrete Conflux resources.
pub fn build_risk_host(config: RiskHostConfig) -> Result<crate::RiskHost, String> {
    let mut application =
        compose_risk_application(config.actor_id.clone(), config.policies, config.state_path)?;
    application
        .set_maintenance_interval(config.interval)
        .map_err(|error| error.to_string())?;
    let snapshot_publisher = kairos_risk_contract::MmapRiskSnapshotPublisher::create(
        config.snapshot_path,
        config.snapshot_slot_size,
        config.actor_id.clone(),
    )
    .map_err(|error| error.to_string())?;
    let event_publisher = kairos_risk_contract::RiskAeronEventPublisher::connect(
        &kairos_risk_contract::AeronEndpoint::from_parts(
            config.aeron_dir.as_deref(),
            config.event_channel,
            config.event_stream_id,
        )
        .map_err(|error| error.to_string())?,
        config.actor_id,
        config.identity,
    )
    .map_err(|error| error.to_string())?;

    let mut system = ConfluxSystem::new();
    system
        .risk_snapshot_publishers
        .ensure_with(RISK_VIEW_RESOURCE.into(), 1, || snapshot_publisher)
        .map_err(|error| error.to_string())?;
    system
        .risk_event_publishers
        .ensure_with(RISK_EVENT_RESOURCE.into(), 1, || event_publisher)
        .map_err(|error| error.to_string())?;

    Ok(crate::RiskHost::new(
        application,
        system,
        config.socket_path,
        config.interval,
        config.health_file,
    )?
    .with_replay_clock(config.replay_clock))
}

pub fn compose_risk_application(
    actor_id: impl Into<String>,
    policies: Vec<RiskPolicy>,
    state_path: Option<PathBuf>,
) -> Result<RiskApplication, String> {
    let store = state_path
        .map(crate::services::persistence::JournalRiskStore::new)
        .map(|store| Box::new(store) as Box<dyn crate::services::persistence::RiskStateStore>);
    let actor = RiskActor::new(actor_id, policies, store)?;
    Ok(RiskApplication::new(actor))
}

/// Test/diagnostic encoder. Production publication is owned by the concrete
/// publishers stored in `ConfluxSystem`.
pub struct FlatbuffersRiskSnapshotWriter {
    inner: kairos_risk_contract::FlatbuffersRiskSnapshotWriter,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskSnapshotWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, snapshot: &crate::RiskCurrentView) -> Result<(), String> {
        self.inner
            .publish(&crate::application::contract::current_view(snapshot))?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}

/// Test/diagnostic encoder. Production publication is owned by the concrete
/// publishers stored in `ConfluxSystem`.
pub struct FlatbuffersRiskEventWriter {
    inner: kairos_risk_contract::FlatbuffersRiskEventWriter,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskEventWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskEventWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn new_with_identity(
        actor_id: impl Into<String>,
        identity: kairos_protocol::InstanceIdentity,
    ) -> Self {
        Self {
            inner: kairos_risk_contract::FlatbuffersRiskEventWriter::new_with_identity(
                actor_id, identity,
            ),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, event: &crate::RiskEvent) -> Result<(), String> {
        self.inner
            .publish(&crate::application::contract::event(event))?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}
