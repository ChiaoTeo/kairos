use super::defaults::*;
use super::sources::MarketSourceBinding;
use kairos_workspace::Workspace;
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct MarketConfig {
    #[serde(default)]
    pub sources: BTreeMap<String, MarketSourceBinding>,
    #[serde(default)]
    pub profiles: BTreeMap<String, MarketProfileConfig>,
    #[serde(default)]
    pub collections: BTreeMap<String, MarketCollectionConfig>,
    pub default_profile: Option<String>,
}

impl MarketConfig {
    pub fn load(workspace: &Workspace) -> Result<Self, String> {
        workspace
            .read_section("market")
            .map_err(|error| error.to_string())
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct MarketCollectionConfig {
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    pub subject: String,
    #[serde(default)]
    pub market_id: Option<String>,
    #[serde(default)]
    pub instrument_id: Option<String>,
    #[serde(default)]
    pub market_data_access_id: Option<String>,
    #[serde(default)]
    pub selectors: Vec<String>,
    #[serde(default)]
    pub exchange: Option<String>,
    #[serde(default)]
    pub market_type: Option<String>,
    #[serde(default)]
    pub asset_type: Option<String>,
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default = "default_collection_queue_capacity")]
    pub queue_capacity: usize,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum MarketRuntimeScopeConfig {
    Shared,
    Instance,
    Replay,
    Diagnostic,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum MarketReplayClockConfig {
    #[default]
    Maximum,
    EventTime,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct MarketReplayConfigDto {
    #[serde(default)]
    pub start_unix_nanos: Option<u64>,
    #[serde(default)]
    pub end_unix_nanos: Option<u64>,
    #[serde(default)]
    pub clock: MarketReplayClockConfig,
    #[serde(default = "one")]
    pub speed_multiplier: u32,
    #[serde(default)]
    pub start_paused: bool,
}

impl Default for MarketReplayConfigDto {
    fn default() -> Self {
        Self {
            start_unix_nanos: None,
            end_unix_nanos: None,
            clock: MarketReplayClockConfig::Maximum,
            speed_multiplier: 1,
            start_paused: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct MarketProfileConfig {
    pub scope: MarketRuntimeScopeConfig,
    #[serde(default = "default_source_input_capacity")]
    pub source_input_capacity: usize,
    #[serde(default = "default_publication_queue_capacity")]
    pub publication_queue_capacity: usize,
    #[serde(default = "default_snapshot_interval_ms")]
    pub snapshot_interval_ms: u64,
    #[serde(default = "default_freshness_check_interval_ms")]
    pub freshness_check_interval_ms: u64,
    #[serde(default = "default_freshness_max_age_ms")]
    pub freshness_max_age_ms: u64,
    #[serde(default = "default_reference_recovery_interval_ms")]
    pub reference_recovery_interval_ms: u64,
    #[serde(default = "default_shutdown_timeout_ms")]
    pub shutdown_timeout_ms: u64,
    #[serde(default)]
    pub replay: Option<MarketReplayConfigDto>,
}
