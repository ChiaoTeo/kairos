use std::path::PathBuf;
use std::time::Duration;

use kairos_conflux::{
    AeronOutputDeclaration, Conflux, ConfluxConfig, ConfluxSystem, HttpControlConfig,
    HttpControlledConflux, MmapOutputDeclaration,
};
use kairos_risk_contract::{RiskHttpControl, RiskViewKey, RiskViewPublisher};

use crate::RiskApplication;
use crate::domain::RiskPolicy;
use crate::services::actor::RiskActor;

pub type RiskHost = HttpControlledConflux<RiskApplication, RiskHttpControl>;

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
    pub identity: kairos_primitives::runtime::InstanceIdentity,
}

/// Assemble the Risk Actor/Contract and its concrete Conflux resources.
pub fn build_risk_host(config: RiskHostConfig) -> Result<RiskHost, String> {
    let mut application =
        compose_risk_application(config.actor_id.clone(), config.policies, config.state_path)?;
    application
        .set_maintenance_interval(config.interval)
        .map_err(|error| error.to_string())?;
    let event_endpoint = kairos_risk_contract::AeronEndpoint::from_parts(
        config.aeron_dir.as_deref(),
        config.event_channel,
        config.event_stream_id,
    )
    .map_err(|error| error.to_string())?;
    let view_key = RiskViewKey::latest(config.actor_id.clone());
    let snapshot_path = RiskViewPublisher::resolved_path(&config.snapshot_path, &view_key)
        .map_err(|error| error.to_string())?;
    application.configure_publication_identity(config.identity);
    let mut system = ConfluxSystem::new();
    system.outputs().mmap.declare(
        "risk-latest".to_owned(),
        MmapOutputDeclaration {
            path: snapshot_path,
            slot_capacity: config.snapshot_slot_size,
            revision: 1,
        },
    )
    .map_err(|error| error.to_string())?;
    system.outputs().aeron.declare(
        "risk-events".to_owned(),
        AeronOutputDeclaration {
            endpoint: event_endpoint,
            revision: 1,
        },
    )
    .map_err(|error| error.to_string())?;

    application.set_clock_mode(if config.replay_clock {
        crate::RiskClockMode::Replay
    } else {
        crate::RiskClockMode::Wall
    });
    let (conflux, handle) = Conflux::new(
        application,
        system,
        ConfluxConfig {
            ingress_capacity: 256,
            ..ConfluxConfig::default()
        },
    )
    .map_err(|error| error.to_string())?;
    let control = HttpControlConfig::uds(config.socket_path).with_health_file(config.health_file);
    Ok(conflux.with_http_control(handle, RiskHttpControl, control))
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
        identity: kairos_primitives::runtime::InstanceIdentity,
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

#[cfg(test)]
mod tests {
    use kairos_conflux::{Conflux, ConfluxConfig, ConfluxSystem, HttpControlConfig};
    use kairos_risk_contract::RiskHttpControl;
    use kairos_workspace::RestControlClient;

    #[tokio::test(flavor = "current_thread")]
    async fn framework_owned_uds_control_preserves_the_risk_contract() {
        let directory = tempfile::tempdir().unwrap();
        let socket = directory.path().join("risk.sock");
        let mut application = super::compose_risk_application("risk", Vec::new(), None).unwrap();
        application.set_clock_mode(crate::RiskClockMode::Replay);
        let (conflux, handle) =
            Conflux::new(application, ConfluxSystem::new(), ConfluxConfig::default()).unwrap();
        let runtime =
            conflux.with_http_control(handle, RiskHttpControl, HttpControlConfig::uds(&socket));

        tokio::task::LocalSet::new()
            .run_until(async move {
                let task = tokio::task::spawn_local(runtime.run());
                for _ in 0..100 {
                    if socket.exists() {
                        break;
                    }
                    tokio::task::yield_now().await;
                }
                let client = RestControlClient::new(&socket);
                let health = client.health().await.unwrap();
                assert_eq!(health["status"], "ready");

                let policy = r#"{"policy":{"policy_id":"account-notional","version":1,"scope":{"account_id":"main","strategy_id":null,"instrument_id":null,"exchange_id":null},"metric":"notional","limit":"100","enforcement":"reject","valid_from_unix_nanos":0,"valid_until_unix_nanos":null}}"#;
                let configured = client
                    .request_json(
                        "POST",
                        "/v1/publish_policy",
                        Some(policy.as_bytes()),
                    )
                    .await
                    .unwrap();
                assert_eq!(configured["status"], "active");

                let request = r#"{"request_id":"request-1","idempotency_key":"key-1","reservation_id":"reservation-1","account_id":"main","strategy_id":"strategy","instrument_id":"instrument","exchange_id":"exchange","proposal":{"notional":"40","initial_margin_rate_bps":10000,"account_segment":"usd_m_futures","collateral_asset":"USDT","reduce_only":false,"margin_rule_id":"test:fully-funded"},"at_unix_nanos":1,"reservation_ttl_nanos":100,"dependency_generation":1,"dependency_event_sequence":1}"#;
                let decision = client
                    .request_json(
                        "POST",
                        "/v1/authorizations",
                        Some(request.as_bytes()),
                    )
                    .await
                    .unwrap();
                assert_eq!(decision["allowed"], true);
                assert_eq!(decision["instrument_id"], "instrument");

                let stop = client
                    .request_json("POST", "/v1/stop", None)
                    .await
                    .unwrap();
                assert_eq!(stop["status"], "stopping");
                task.await.unwrap().unwrap();
                assert!(!socket.exists());
            })
            .await;
    }
}
