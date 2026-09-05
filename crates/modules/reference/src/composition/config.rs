//! Reference-owned workspace configuration schema.

use std::collections::BTreeMap;

use serde::Deserialize;

use crate::domain::{ReferenceError, ReferenceResult, SourceTickBudget};

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
    /// Read-only migration input for workspaces created before connection
    /// profiles and the durable source registry became authoritative.
    #[serde(default, rename = "providers")]
    legacy_providers: BTreeMap<String, LegacyReferenceProviderConfig>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub(crate) struct LegacyReferenceProviderConfig {
    #[serde(default = "enabled_by_default")]
    pub(crate) enabled: bool,
    pub(crate) endpoint: Option<String>,
    pub(crate) credential_id: Option<String>,
    pub(crate) product: Option<String>,
    #[serde(default)]
    pub(crate) products: Vec<String>,
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
        for (provider, config) in &self.legacy_providers {
            let allowed: &[&str] = match provider.as_str() {
                "binance" => &[
                    "spot",
                    "usd-m-futures",
                    "coin-m-futures",
                    "options",
                    "equity",
                ],
                "okx" => &["spot", "margin", "swap", "futures", "options"],
                "hyperliquid" => &["spot", "perpetual"],
                "massive" => &["equity", "options"],
                _ => return Err(format!("unsupported legacy Reference provider {provider}")),
            };
            for product in config.product.iter().chain(config.products.iter()) {
                if !allowed.contains(&product.as_str()) {
                    return Err(format!(
                        "unsupported legacy Reference product {provider}/{product}"
                    ));
                }
            }
        }
        Ok(())
    }

    pub(crate) fn legacy_providers(&self) -> &BTreeMap<String, LegacyReferenceProviderConfig> {
        &self.legacy_providers
    }
}

fn enabled_by_default() -> bool {
    true
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
    fn legacy_provider_configuration_is_accepted_only_as_migration_input() {
        assert!(
            serde_json::from_str::<ReferenceConfig>(
                r#"{"providers":{"unknown":{"enabled":true}}}"#
            )
            .unwrap()
            .validate()
            .is_err()
        );
        let okx: ReferenceConfig =
            serde_json::from_str(r#"{"providers":{"okx":{"product":"swap"}}}"#).unwrap();
        okx.validate().unwrap();
        assert!(
            serde_json::from_str::<ReferenceConfig>(
                r#"{"products":{"binance":{"equity":{"enabled":true}}}}"#
            )
            .is_err()
        );

        let legacy = serde_json::from_str::<ReferenceConfig>(
            r#"{"providers":{"massive":{"enabled":true,"credential_id":"massive-readonly"}}}"#,
        )
        .unwrap();
        assert_eq!(
            legacy.legacy_providers()["massive"]
                .credential_id
                .as_deref(),
            Some("massive-readonly")
        );
    }

    #[test]
    fn zero_configuration_only_defines_runtime_policy() {
        let config = ReferenceConfig::default();

        assert_eq!(config.runtime.refresh_interval().as_secs(), 300);
        config.validate().unwrap();
    }

    #[test]
    fn advanced_configuration_is_nested_below_runtime() {
        let config: ReferenceConfig = toml::from_str(
            r#"
            [runtime]
            refresh_interval_seconds = 60

            "#,
        )
        .unwrap();

        assert_eq!(config.runtime.refresh_interval().as_secs(), 60);
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
