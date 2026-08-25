//! Secret-free Workspace profile for one provider connection.

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
        if parsed.version != 1 {
            return Err(format!(
                "provider connection {connection_id} version must be 1"
            ));
        }
        let profile = parsed.connection;
        profile.validate(connection_id)?;
        Ok(profile)
    }

    pub fn canonical_root(workspace_root: &Path) -> PathBuf {
        workspace_root.join("config/market/connections")
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
            r#"version = 1

[connection]
connection_id = "massive"
provider = "massive"
environment = "production"
endpoint = "https://api.massive.com"
credential_id = "massive-readonly"
enabled = true
products = ["reference", "equity"]
purposes = ["reference-catalog", "market-query"]
"#,
        )
        .unwrap();

        let profile = ProviderConnectionProfile::load(directory.path(), "massive").unwrap();
        profile
            .require("massive", Some("equity"), "market-query")
            .unwrap();
        assert!(
            profile
                .require("massive", Some("options"), "market-query")
                .is_err()
        );
    }
}
