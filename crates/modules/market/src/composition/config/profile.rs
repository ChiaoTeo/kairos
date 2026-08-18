use super::dto::*;
use kairos_workspace::Workspace;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketHostRequest {
    pub workspace: PathBuf,
    pub launch_mode: String,
    pub launch_id: Option<String>,
    pub instance_id: String,
    pub runtime_profile: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketRuntimeScope {
    Shared,
    Instance,
    Replay,
    Diagnostic,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MarketReplayClock {
    Maximum,
    EventTime,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketReplayConfig {
    pub start_unix_nanos: Option<u64>,
    pub end_unix_nanos: Option<u64>,
    pub clock: MarketReplayClock,
    pub speed_multiplier: u32,
    pub start_paused: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketRuntimeProfile {
    pub name: String,
    pub scope: MarketRuntimeScope,
    pub source_input_capacity: usize,
    pub publication_queue_capacity: usize,
    pub snapshot_interval: Duration,
    pub freshness_check_interval: Duration,
    pub freshness_max_age: Duration,
    pub reference_recovery_interval: Duration,
    pub shutdown_timeout: Duration,
    pub replay: Option<MarketReplayConfig>,
}

impl MarketRuntimeProfile {
    pub fn resolve(
        workspace: &Workspace,
        selector: Option<&str>,
        has_instance: bool,
    ) -> Result<Self, String> {
        let market = MarketConfig::load(workspace)?;
        if market.default_profile.is_none() && market.profiles.is_empty() {
            if has_instance {
                return Err(
                    "Market runtime profile is required for an instance-scoped process".into(),
                );
            }
            return Ok(Self::default_shared(
                selector
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or("shared-live"),
            ));
        }
        let name = selector
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or(market.default_profile.as_deref())
            .ok_or_else(|| {
                "Market runtime profile is required (--runtime-profile or market.default_profile)"
                    .to_string()
            })?;
        let configured = market
            .profiles
            .get(name)
            .ok_or_else(|| format!("unknown Market runtime profile: {name}"))?;
        Self::from_workspace(name, configured, workspace, has_instance)
    }

    fn default_shared(name: &str) -> Self {
        Self {
            name: name.to_owned(),
            scope: MarketRuntimeScope::Shared,
            source_input_capacity: 10_000,
            publication_queue_capacity: 256,
            snapshot_interval: Duration::from_secs(1),
            freshness_check_interval: Duration::from_millis(250),
            freshness_max_age: Duration::from_secs(5),
            reference_recovery_interval: Duration::from_secs(1),
            shutdown_timeout: Duration::from_secs(5),
            replay: None,
        }
    }

    fn from_workspace(
        name: &str,
        configured: &MarketProfileConfig,
        _workspace: &Workspace,
        has_instance: bool,
    ) -> Result<Self, String> {
        let scope = match configured.scope {
            MarketRuntimeScopeConfig::Shared => MarketRuntimeScope::Shared,
            MarketRuntimeScopeConfig::Instance => MarketRuntimeScope::Instance,
            MarketRuntimeScopeConfig::Replay => MarketRuntimeScope::Replay,
            MarketRuntimeScopeConfig::Diagnostic => MarketRuntimeScope::Diagnostic,
        };
        match (scope, has_instance) {
            (MarketRuntimeScope::Shared, true) => {
                return Err(format!(
                    "Market profile {name} is shared but launch/instance identity was supplied"
                ));
            }
            (MarketRuntimeScope::Instance | MarketRuntimeScope::Replay, false) => {
                return Err(format!(
                    "Market profile {name} requires launch/instance identity"
                ));
            }
            _ => {}
        }
        for (field, value) in [
            ("source_input_capacity", configured.source_input_capacity),
            (
                "publication_queue_capacity",
                configured.publication_queue_capacity,
            ),
        ] {
            if value == 0 {
                return Err(format!("Market profile {name} {field} must be positive"));
            }
        }
        for (field, value) in [
            ("snapshot_interval_ms", configured.snapshot_interval_ms),
            (
                "freshness_check_interval_ms",
                configured.freshness_check_interval_ms,
            ),
            ("freshness_max_age_ms", configured.freshness_max_age_ms),
            (
                "reference_recovery_interval_ms",
                configured.reference_recovery_interval_ms,
            ),
            ("shutdown_timeout_ms", configured.shutdown_timeout_ms),
        ] {
            if value == 0 {
                return Err(format!("Market profile {name} {field} must be positive"));
            }
        }

        let replay = match (scope, configured.replay.as_ref()) {
            (MarketRuntimeScope::Replay, configured) => {
                let configured = configured.cloned().unwrap_or_default();
                if configured
                    .start_unix_nanos
                    .zip(configured.end_unix_nanos)
                    .is_some_and(|(start, end)| start > end)
                {
                    return Err(format!(
                        "Market replay profile {name} start_unix_nanos must not exceed end_unix_nanos"
                    ));
                }
                if configured.speed_multiplier == 0 {
                    return Err(format!(
                        "Market replay profile {name} speed_multiplier must be positive"
                    ));
                }
                Some(MarketReplayConfig {
                    start_unix_nanos: configured.start_unix_nanos,
                    end_unix_nanos: configured.end_unix_nanos,
                    clock: match configured.clock {
                        MarketReplayClockConfig::Maximum => MarketReplayClock::Maximum,
                        MarketReplayClockConfig::EventTime => MarketReplayClock::EventTime,
                    },
                    speed_multiplier: configured.speed_multiplier,
                    start_paused: configured.start_paused,
                })
            }
            (_, Some(_)) => {
                return Err(format!(
                    "Market profile {name} configures replay policy outside replay scope"
                ));
            }
            (_, None) => None,
        };

        Ok(Self {
            name: name.to_owned(),
            scope,
            source_input_capacity: configured.source_input_capacity,
            publication_queue_capacity: configured.publication_queue_capacity,
            snapshot_interval: Duration::from_millis(configured.snapshot_interval_ms),
            freshness_check_interval: Duration::from_millis(configured.freshness_check_interval_ms),
            freshness_max_age: Duration::from_millis(configured.freshness_max_age_ms),
            reference_recovery_interval: Duration::from_millis(
                configured.reference_recovery_interval_ms,
            ),
            shutdown_timeout: Duration::from_millis(configured.shutdown_timeout_ms),
            replay,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::{MarketRuntimeProfile, MarketRuntimeScope};
    use kairos_workspace::Workspace;

    fn workspace(manifest: &str) -> (tempfile::TempDir, Workspace) {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("workspace.toml"), manifest).unwrap();
        let workspace = Workspace::open(root.path()).unwrap();
        (root, workspace)
    }

    #[test]
    fn live_profile_has_no_source_policy() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"

[market]
default_profile = "live"

[market.profiles.live]
scope = "shared"
"#,
        );
        let profile = MarketRuntimeProfile::resolve(&workspace, None, false).unwrap();
        assert_eq!(profile.scope, MarketRuntimeScope::Shared);
    }

    #[test]
    fn workspace_without_market_runtime_uses_shared_demand_driven_defaults() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"
"#,
        );
        let profile = MarketRuntimeProfile::resolve(&workspace, None, false).unwrap();
        assert_eq!(profile.name, "shared-live");
        assert_eq!(profile.scope, MarketRuntimeScope::Shared);
    }

    #[test]
    fn replay_profile_types_window_clock_speed_and_pause_policy() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"

[market]
default_profile = "replay"

[market.profiles.replay]
scope = "replay"

[market.profiles.replay.replay]
start_unix_nanos = 10
end_unix_nanos = 20
clock = "event-time"
speed_multiplier = 4
start_paused = true
"#,
        );
        let profile = MarketRuntimeProfile::resolve(&workspace, None, true).unwrap();
        let replay = profile.replay.expect("replay policy");
        assert_eq!(replay.start_unix_nanos, Some(10));
        assert_eq!(replay.end_unix_nanos, Some(20));
        assert_eq!(replay.clock, super::MarketReplayClock::EventTime);
        assert_eq!(replay.speed_multiplier, 4);
        assert!(replay.start_paused);
    }

    #[test]
    fn replay_profile_rejects_zero_speed() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"

[market]
default_profile = "replay"

[market.profiles.replay]
scope = "replay"

[market.profiles.replay.replay]
speed_multiplier = 0
"#,
        );
        assert!(MarketRuntimeProfile::resolve(&workspace, None, true)
            .unwrap_err()
            .contains("speed_multiplier must be positive"));
    }

    #[test]
    fn rejects_scope_mismatch_and_missing_source() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"

[market]
default_profile = "instance"

[market.profiles.instance]
scope = "instance"
"#,
        );
        assert!(MarketRuntimeProfile::resolve(&workspace, None, false)
            .unwrap_err()
            .contains("requires launch/instance identity"));
        MarketRuntimeProfile::resolve(&workspace, None, true).unwrap();
    }

    #[test]
    fn diagnostic_profile_is_a_valid_empty_runtime() {
        let (_root, workspace) = workspace(
            r#"
version = 1
workspace_id = "demo"

[market]
default_profile = "diagnostic"

[market.profiles.diagnostic]
scope = "diagnostic"
"#,
        );
        assert_eq!(
            MarketRuntimeProfile::resolve(&workspace, None, false)
                .unwrap()
                .scope,
            MarketRuntimeScope::Diagnostic
        );
    }
}
