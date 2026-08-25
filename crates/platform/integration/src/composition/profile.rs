//! Secret-free Workspace profile for one provider connection.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProviderConnectionDocument {
    pub version: u32,
    pub connection: ProviderConnectionProfile,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProviderConnectionProfile {
    pub connection_id: String,
    pub provider: String,
    pub environment: String,
    pub endpoint: String,
    #[serde(default)]
    pub endpoints: BTreeMap<String, String>,
    pub credential_id: String,
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    #[serde(default)]
    pub products: Vec<String>,
    #[serde(default)]
    pub purposes: Vec<String>,
}

impl ProviderConnectionProfile {
    pub fn load(root: &Path, connection_id: &str) -> Result<Self, String> {
        validate_id(connection_id)?;
        let path = root.join(format!("{connection_id}.toml"));
        let document = std::fs::read_to_string(&path).map_err(|error| {
            format!("provider connection {connection_id} cannot be read: {error}")
        })?;
        let parsed: ProviderConnectionDocument = toml::from_str(&document)
            .map_err(|error| format!("provider connection {connection_id} is invalid: {error}"))?;
        if !matches!(parsed.version, 1 | 2) {
            return Err(format!(
                "provider connection {connection_id} version must be 1 or 2"
            ));
        }
        let profile = parsed.connection;
        profile.validate(connection_id)?;
        Ok(profile)
    }

    pub fn canonical_root(workspace_root: &Path) -> PathBuf {
        workspace_root.join("config/integration/provider-connections")
    }

    pub fn require(
        &self,
        provider: &str,
        product: Option<&str>,
        purpose: &str,
    ) -> Result<(), String> {
        if !self.enabled {
            return Err(format!(
                "provider connection {} is disabled",
                self.connection_id
            ));
        }
        if self.provider != provider {
            return Err(format!(
                "provider connection {} belongs to {}, expected {provider}",
                self.connection_id, self.provider
            ));
        }
        if product.is_some_and(|value| !self.products.iter().any(|item| item == value)) {
            return Err(format!(
                "provider connection {} does not enable product {}",
                self.connection_id,
                product.expect("checked")
            ));
        }
        if !self.purposes.iter().any(|value| value == purpose) {
            return Err(format!(
                "provider connection {} does not allow purpose {purpose}",
                self.connection_id
            ));
        }
        Ok(())
    }

    pub fn endpoint_for(&self, purpose: &str, product: Option<&str>) -> Option<&str> {
        product
            .and_then(|product| self.endpoints.get(&format!("{purpose}:{product}")))
            .or_else(|| self.endpoints.get(purpose))
            .or_else(|| self.endpoints.get("default"))
            .map(String::as_str)
            .or_else(|| {
                (self.endpoints.is_empty() || purpose != "market-stream")
                    .then_some(self.endpoint.as_str())
            })
    }

    fn validate(&self, requested_id: &str) -> Result<(), String> {
        validate_id(&self.connection_id)?;
        if self.connection_id != requested_id {
            return Err(format!(
                "provider connection id {} does not match file {requested_id}",
                self.connection_id
            ));
        }
        if self.provider.trim().is_empty()
            || self.environment.trim().is_empty()
            || self.credential_id.trim().is_empty()
        {
            return Err(format!(
                "provider connection {requested_id} has incomplete identity"
            ));
        }
        if !self.endpoint.starts_with("https://") {
            return Err(format!(
                "provider connection {requested_id} endpoint must use HTTPS"
            ));
        }
        for (key, endpoint) in &self.endpoints {
            if key.trim().is_empty() || key.chars().any(char::is_whitespace) {
                return Err(format!(
                    "provider connection {requested_id} endpoint keys must be non-empty without spaces"
                ));
            }
            let valid_scheme = if key.starts_with("market-stream") {
                endpoint.starts_with("http://")
                    || endpoint.starts_with("https://")
                    || endpoint.starts_with("ws://")
                    || endpoint.starts_with("wss://")
            } else {
                endpoint.starts_with("https://")
            };
            if !valid_scheme {
                let requirement = if key.starts_with("market-stream") {
                    "HTTP(S) or WS(S)"
                } else {
                    "HTTPS"
                };
                return Err(format!(
                    "provider connection {requested_id} endpoint {key} must use {requirement}"
                ));
            }
        }
        Ok(())
    }
}

fn enabled_by_default() -> bool {
    true
}

fn validate_id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 64
        || !value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'-' | b'_'))
        })
    {
        return Err("provider connection id must be a path-safe lowercase identifier".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::ProviderConnectionProfile;

    #[test]
    fn loads_and_checks_a_secret_free_profile() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("massive.toml"),
            r#"version = 2

[connection]
connection_id = "massive"
provider = "massive"
environment = "production"
endpoint = "https://api.massive.com"
credential_id = "massive-readonly"
enabled = true
products = ["equity"]
purposes = ["reference-catalog", "market-query"]

[connection.endpoints]
"reference-catalog" = "https://reference.massive.com"
"#,
        )
        .unwrap();

        let profile = ProviderConnectionProfile::load(directory.path(), "massive").unwrap();
        profile
            .require("massive", Some("equity"), "market-query")
            .unwrap();
        assert_eq!(
            profile.endpoint_for("reference-catalog", Some("equity")),
            Some("https://reference.massive.com")
        );
        assert_eq!(profile.endpoint_for("market-stream", Some("equity")), None);
        assert!(
            profile
                .require("massive", Some("options"), "market-query")
                .is_err()
        );
    }
}
