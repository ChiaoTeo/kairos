//! Reference-owned workspace configuration schema.

use std::collections::BTreeMap;

use serde::Deserialize;

use crate::domain::{ReferenceError, ReferenceResult, SourceTickBudget};

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct ReferenceProviderConfig {
    pub enabled: Option<bool>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct ReferenceProductConfig {
    pub enabled: Option<bool>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct ReferenceParticipantConfig {
    #[serde(rename = "type")]
    pub entity_type: String,
    pub name: String,
    pub enabled: Option<bool>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct ReferenceRuntimeConfig {
    #[serde(default)]
    pub tick_budget: ReferenceTickBudgetConfig,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
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
pub struct ReferenceConfig {
    #[serde(default)]
    pub runtime: ReferenceRuntimeConfig,
    #[serde(default)]
    pub providers: BTreeMap<String, ReferenceProviderConfig>,
    #[serde(default)]
    pub products: BTreeMap<String, BTreeMap<String, ReferenceProductConfig>>,
    #[serde(default)]
    pub participants: BTreeMap<String, ReferenceParticipantConfig>,
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
        for provider in self.providers.keys().chain(self.products.keys()) {
            if !matches!(
                provider.as_str(),
                "binance" | "okx" | "hyperliquid" | "massive"
            ) {
                return Err(format!("unsupported Reference provider: {provider}"));
            }
        }
        for (provider, products) in &self.products {
            for product in products.keys() {
                let supported = match provider.as_str() {
                    "binance" => matches!(
                        product.as_str(),
                        "spot" | "usd-m-futures" | "coin-m-futures" | "options" | "equity"
                    ),
                    "okx" => matches!(
                        product.as_str(),
                        "spot" | "margin" | "swap" | "futures" | "options"
                    ),
                    "hyperliquid" => matches!(product.as_str(), "spot" | "perpetual"),
                    "massive" => matches!(product.as_str(), "equity" | "options"),
                    _ => false,
                };
                if !supported {
                    return Err(format!(
                        "unsupported Reference provider product: {provider}/{product}"
                    ));
                }
            }
        }
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
            "reference runtime tick budget {field} must be greater than zero"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::ReferenceConfig;

    #[test]
    fn rejects_unknown_provider_and_product_before_composition() {
        let unknown_provider: ReferenceConfig =
            serde_json::from_str(r#"{"providers":{"unknown":{"enabled":true}}}"#).unwrap();
        assert!(unknown_provider.validate().is_err());

        let wrong_product: ReferenceConfig =
            serde_json::from_str(r#"{"products":{"okx":{"usd-m-futures":{"enabled":true}}}}"#)
                .unwrap();
        assert!(wrong_product.validate().is_err());
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

        let invalid: ReferenceConfig = toml::from_str(
            r#"
            [runtime.tick_budget]
            max_sources_per_tick = 0
            "#,
        )
        .unwrap();
        assert!(invalid.validate().is_err());
    }
}
