//! Reference-owned workspace configuration schema.

use std::collections::BTreeMap;

use serde::Deserialize;

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
pub struct ReferenceConfig {
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
}
