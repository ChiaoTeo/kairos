//! Reference-owned workspace configuration schema.

use serde::Deserialize;

use crate::domain::{ReferenceError, ReferenceResult, SourceTickBudget};

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceProviders {
    #[serde(default = "default_binance_provider")]
    pub binance: BinanceReferenceProvider,
    #[serde(default = "default_public_provider")]
    pub okx: PublicReferenceProvider,
    #[serde(default = "default_public_provider")]
    pub hyperliquid: PublicReferenceProvider,
    #[serde(default)]
    pub massive: CredentialedReferenceProvider,
}

impl Default for ReferenceProviders {
    fn default() -> Self {
        Self {
            binance: default_binance_provider(),
            okx: default_public_provider(),
            hyperliquid: default_public_provider(),
            massive: CredentialedReferenceProvider::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BinanceReferenceProvider {
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    /// When present, also enables the credentialed Binance equity catalog.
    pub credential_id: Option<String>,
    #[serde(default)]
    pub endpoints: BinanceReferenceEndpoints,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PublicReferenceProvider {
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CredentialedReferenceProvider {
    #[serde(default)]
    pub enabled: bool,
    /// Integration-owned connection profile. Legacy credential/endpoint fields
    /// remain readable during the workspace migration.
    pub connection_id: Option<String>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BinanceReferenceEndpoints {
    pub spot: Option<String>,
    pub usd_m_futures: Option<String>,
    pub coin_m_futures: Option<String>,
    pub options: Option<String>,
    pub equity: Option<String>,
}

const fn enabled_by_default() -> bool {
    true
}

fn default_binance_provider() -> BinanceReferenceProvider {
    BinanceReferenceProvider {
        enabled: true,
        credential_id: None,
        endpoints: BinanceReferenceEndpoints::default(),
    }
}

fn default_public_provider() -> PublicReferenceProvider {
    PublicReferenceProvider {
        enabled: true,
        endpoint: None,
    }
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceRuntimeConfig {
    /// Optional advanced override. Zero-configuration runtime uses five minutes.
    pub refresh_interval_seconds: Option<u64>,
    #[serde(default)]
    pub tick_budget: ReferenceTickBudgetConfig,
}

impl ReferenceRuntimeConfig {
    pub fn refresh_interval(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.refresh_interval_seconds.unwrap_or(300))
    }
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceTickBudgetConfig {
    pub max_sources_per_tick: Option<u32>,
    pub max_batches_per_source: Option<u32>,
    pub max_records_per_batch: Option<u64>,
    pub max_wall_clock_millis: Option<u64>,
    pub max_publications_per_tick: Option<u32>,
}

impl ReferenceTickBudgetConfig {
    pub fn to_domain(&self) -> ReferenceResult<SourceTickBudget> {
        validate_non_zero_u32(self.max_sources_per_tick, "max_sources_per_tick")?;
        validate_non_zero_u32(self.max_batches_per_source, "max_batches_per_source")?;
        validate_non_zero_u64(self.max_records_per_batch, "max_records_per_batch")?;
        validate_non_zero_u64(self.max_wall_clock_millis, "max_wall_clock_millis")?;
        validate_non_zero_u32(self.max_publications_per_tick, "max_publications_per_tick")?;

        let default = SourceTickBudget::default();
        Ok(SourceTickBudget {
            max_sources_per_tick: self
                .max_sources_per_tick
                .unwrap_or(default.max_sources_per_tick),
            max_batches_per_source: self
                .max_batches_per_source
                .unwrap_or(default.max_batches_per_source),
            max_records_per_batch: self.max_records_per_batch,
            max_wall_clock_millis: self.max_wall_clock_millis,
            max_publications_per_tick: self.max_publications_per_tick,
        })
    }
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceConfig {
    #[serde(default)]
    pub runtime: ReferenceRuntimeConfig,
    #[serde(default)]
    pub providers: ReferenceProviders,
}

impl ReferenceConfig {
    pub fn load(workspace: &kairos_workspace::Workspace) -> Result<Self, String> {
        let value: Self = workspace
            .read_section("reference")
            .map_err(|error| error.to_string())?;
        value.validate()?;
        Ok(value)
    }

    pub fn validate(&self) -> Result<(), String> {
        self.runtime
            .tick_budget
            .to_domain()
            .map_err(|error| error.to_string())?;
        validate_non_zero_u64(
            self.runtime.refresh_interval_seconds,
            "refresh_interval_seconds",
        )
        .map_err(|error| error.to_string())?;
        Ok(())
    }
}

fn validate_non_zero_u32(value: Option<u32>, field: &str) -> ReferenceResult<()> {
    if value == Some(0) {
        return Err(ReferenceError::Invalid(format!(
            "reference runtime tick budget {field} must be greater than zero"
        )));
    }
    Ok(())
}

fn validate_non_zero_u64(value: Option<u64>, field: &str) -> ReferenceResult<()> {
    if value == Some(0) {
        return Err(ReferenceError::Invalid(format!(
            "reference runtime {field} must be greater than zero"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::ReferenceConfig;

    #[test]
    fn provider_configuration_does_not_expose_source_products() {
        assert!(
            serde_json::from_str::<ReferenceConfig>(
                r#"{"providers":{"unknown":{"enabled":true}}}"#
            )
            .is_err()
        );
        assert!(
            serde_json::from_str::<ReferenceConfig>(r#"{"providers":{"okx":{"product":"swap"}}}"#)
                .is_err()
        );
        assert!(
            serde_json::from_str::<ReferenceConfig>(
                r#"{"products":{"binance":{"equity":{"enabled":true}}}}"#
            )
            .is_err()
        );

        let configured: ReferenceConfig = serde_json::from_str(
            r#"{"providers":{"massive":{"enabled":true,"credential_id":"massive-readonly"}}}"#,
        )
        .unwrap();
        assert!(configured.providers.massive.enabled);
        assert_eq!(
            configured.providers.massive.credential_id.as_deref(),
            Some("massive-readonly")
        );
        configured.validate().unwrap();
    }

    #[test]
    fn zero_configuration_enables_public_providers_only() {
        let config = ReferenceConfig::default();

        assert!(config.providers.binance.enabled);
        assert!(config.providers.okx.enabled);
        assert!(config.providers.hyperliquid.enabled);
        assert!(!config.providers.massive.enabled);
        assert!(config.providers.binance.credential_id.is_none());
        assert_eq!(config.runtime.refresh_interval().as_secs(), 300);
        config.validate().unwrap();
    }

    #[test]
    fn advanced_configuration_is_nested_below_runtime_and_provider() {
        let config: ReferenceConfig = toml::from_str(
            r#"
            [runtime]
            refresh_interval_seconds = 60

            [providers.massive]
            enabled = true
            credential_id = "massive-readonly"
            endpoint = "https://massive.example"

            [providers.binance.endpoints]
            spot = "https://binance.example"
            "#,
        )
        .unwrap();

        assert_eq!(config.runtime.refresh_interval().as_secs(), 60);
        assert_eq!(
            config.providers.massive.credential_id.as_deref(),
            Some("massive-readonly")
        );
        assert_eq!(
            config.providers.binance.endpoints.spot.as_deref(),
            Some("https://binance.example")
        );
        config.validate().unwrap();
    }

    #[test]
    fn parses_and_validates_runtime_tick_budget() {
        let config: ReferenceConfig = toml::from_str(
            r#"
            [runtime.tick_budget]
            max_sources_per_tick = 3
            max_batches_per_source = 5
            max_records_per_batch = 1000
            max_wall_clock_millis = 2500
            max_publications_per_tick = 50
            "#,
        )
        .unwrap();

        let budget = config.runtime.tick_budget.to_domain().unwrap();
        assert_eq!(budget.max_sources_per_tick, 3);
        assert_eq!(budget.max_batches_per_source, 5);
        assert_eq!(budget.max_records_per_batch, Some(1000));
        assert_eq!(budget.max_wall_clock_millis, Some(2500));
        assert_eq!(budget.max_publications_per_tick, Some(50));
        assert_eq!(config.runtime.refresh_interval().as_secs(), 300);

        let invalid: ReferenceConfig = toml::from_str(
            r#"
            [runtime.tick_budget]
            max_sources_per_tick = 0
            "#,
        )
        .unwrap();
        assert!(invalid.validate().is_err());

        let configured: ReferenceConfig = toml::from_str(
            r#"
            [runtime]
            refresh_interval_seconds = 60
            "#,
        )
        .unwrap();
        configured.validate().unwrap();
        assert_eq!(configured.runtime.refresh_interval().as_secs(), 60);
    }
}
