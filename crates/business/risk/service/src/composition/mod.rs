use std::path::PathBuf;

use crate::domain::Budget;
use crate::services::actor::RiskActor;
use crate::RiskApplication;

/// Build the Risk application and select its concrete persistence mode.
///
/// Callers provide business configuration and an optional state path; they do
/// not construct actors or persistence objects directly.
pub fn compose_risk_application(
    actor_id: impl Into<String>,
    budgets: Vec<Budget>,
    allow_unbudgeted: bool,
    state_path: Option<PathBuf>,
) -> Result<RiskApplication, String> {
    let store = state_path
        .map(crate::services::persistence::JsonRiskStore::new)
        .map(|store| Box::new(store) as Box<dyn crate::services::persistence::RiskStateStore>);
    let actor = RiskActor::new(actor_id, budgets, allow_unbudgeted, store)?;
    Ok(RiskApplication::new(actor))
}

pub struct FlatbuffersRiskSnapshotWriter {
    inner: kairos_risk_contract::encoding::FlatbuffersRiskSnapshotWriter,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersRiskSnapshotWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::encoding::FlatbuffersRiskSnapshotWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, snapshot: &crate::RiskSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_risk_contract::RiskSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}

pub struct FlatbuffersRiskEventWriter {
    inner: kairos_risk_contract::encoding::FlatbuffersRiskEventWriter,
    pub last_payload: Option<Vec<u8>>,
}

pub struct MmapRiskSnapshotPublisher {
    inner: kairos_risk_contract::encoding::MmapRiskSnapshotPublisher,
}

impl MmapRiskSnapshotPublisher {
    pub fn create(
        path: impl AsRef<std::path::Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            inner: kairos_risk_contract::encoding::MmapRiskSnapshotPublisher::create(
                path, slot_size, actor_id,
            )
            .map_err(|error| error.to_string())?,
        })
    }

    pub fn publish(&mut self, snapshot: &crate::RiskSnapshot) -> Result<(), String> {
        let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
        let contract: kairos_risk_contract::RiskSnapshot =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner
            .publish(&contract)
            .map_err(|error| error.to_string())
    }
}

impl FlatbuffersRiskEventWriter {
    pub fn new(actor_id: impl Into<String>) -> Self {
        Self {
            inner: kairos_risk_contract::encoding::FlatbuffersRiskEventWriter::new(actor_id),
            last_payload: None,
        }
    }

    pub fn publish(&mut self, event: &crate::RiskEvent) -> Result<(), String> {
        let value = serde_json::to_value(event).map_err(|error| error.to_string())?;
        let contract: kairos_risk_contract::RiskEvent =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        self.inner.publish(&contract)?;
        self.last_payload = self.inner.last_payload.clone();
        Ok(())
    }
}
