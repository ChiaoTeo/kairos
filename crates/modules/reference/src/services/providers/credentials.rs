use kairos_conflux::BinanceCredential;
use kairos_credentials::CredentialStore;
use kairos_integration::composition::ProviderConnectionProfile;

use crate::domain::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Default)]
pub(crate) struct ReferenceCredentialResolver {
    binance: std::collections::BTreeMap<String, BinanceCredential>,
    massive: std::collections::BTreeMap<String, String>,
    connections: std::collections::BTreeMap<String, ProviderConnectionProfile>,
}

impl ReferenceCredentialResolver {
    pub(crate) fn from_store(store: CredentialStore) -> Self {
        let mut resolver = Self::default();
        for record in store.credentials {
            match record.provider().trim().to_ascii_lowercase().as_str() {
                "binance" => {
                    let Some(api_key) = record.api_key_value() else {
                        continue;
                    };
                    if api_key.trim().is_empty() {
                        continue;
                    }
                    let Some(secret) = record.secret_value() else {
                        continue;
                    };
                    resolver.insert_binance(
                        record.credential_id(),
                        BinanceCredential {
                            principal_id: record.credential_id().to_owned(),
                            api_key: secrecy::SecretString::new(api_key.into()),
                            secret: secrecy::SecretString::new(secret.into()),
                        },
                    );
                },
                "massive" => {
                    let Some(api_key) = record.api_key_value() else {
                        continue;
                    };
                    resolver.insert_massive(record.credential_id(), api_key);
                },
                _ => {},
            }
        }
        resolver
    }

    pub(crate) fn load_connection_profiles(
        mut self,
        root: &std::path::Path,
    ) -> ReferenceResult<Self> {
        if !root.exists() {
            return Ok(self);
        }
        let entries =
            std::fs::read_dir(root).map_err(|error| ReferenceError::Provider(error.to_string()))?;
        for entry in entries {
            let entry = entry.map_err(|error| ReferenceError::Provider(error.to_string()))?;
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) != Some("toml") {
                continue;
            }
            let Some(connection_id) = path.file_stem().and_then(|value| value.to_str()) else {
                continue;
            };
            let profile = ProviderConnectionProfile::load(root, connection_id)
                .map_err(ReferenceError::Provider)?;
            self.connections.insert(connection_id.to_owned(), profile);
        }
        Ok(self)
    }

    #[cfg(test)]
    pub(crate) fn insert_connection(&mut self, profile: ProviderConnectionProfile) {
        self.connections
            .insert(profile.connection_id.clone(), profile);
    }

    pub(crate) fn insert_binance(&mut self, binding: &str, credential: BinanceCredential) {
        let binding = binding.trim();
        if binding.is_empty() {
            return;
        }
        self.binance
            .insert(binding.to_ascii_lowercase(), credential.clone());
        self.binance.insert(
            format!("binance.{binding}").to_ascii_lowercase(),
            credential,
        );
    }

    pub(crate) fn insert_massive(&mut self, binding: &str, api_key: impl Into<String>) {
        let binding = binding.trim();
        let api_key = api_key.into();
        if binding.is_empty() || api_key.trim().is_empty() {
            return;
        }
        self.massive
            .insert(binding.to_ascii_lowercase(), api_key.clone());
        self.massive
            .insert(format!("massive.{binding}").to_ascii_lowercase(), api_key);
    }

    pub(super) fn binance(
        &self,
        connection_id: Option<&str>,
        product: &str,
    ) -> ReferenceResult<Option<BinanceCredential>> {
        let Some(profile) = self.connection(connection_id, "binance", product)? else {
            return Ok(None);
        };
        self.binance
            .get(&profile.credential_id.to_ascii_lowercase())
            .cloned()
            .map(Some)
            .ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "Integration connection {} credential {} is not available for Binance",
                    profile.connection_id, profile.credential_id
                ))
            })
    }

    pub(super) fn massive(
        &self,
        connection_id: Option<&str>,
        product: &str,
    ) -> ReferenceResult<Option<String>> {
        let Some(profile) = self.connection(connection_id, "massive", product)? else {
            return Ok(None);
        };
        self.massive
            .get(&profile.credential_id.to_ascii_lowercase())
            .cloned()
            .map(Some)
            .ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "Integration connection {} credential {} is not available for Massive",
                    profile.connection_id, profile.credential_id
                ))
            })
    }

    pub(super) fn endpoint(
        &self,
        connection_id: Option<&str>,
        provider: &str,
        product: &str,
        default: &str,
    ) -> ReferenceResult<String> {
        let Some(profile) = self.connection(connection_id, provider, product)? else {
            return Ok(default.to_owned());
        };
        profile
            .endpoint_for("reference-catalog", Some(product))
            .map(str::to_owned)
            .ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "Integration connection {} has no reference-catalog endpoint for {product}",
                    profile.connection_id
                ))
            })
    }

    pub(super) fn environment(
        &self,
        connection_id: Option<&str>,
        provider: &str,
        product: &str,
    ) -> ReferenceResult<String> {
        Ok(self
            .connection(connection_id, provider, product)?
            .map(|profile| profile.environment.clone())
            .unwrap_or_else(|| "public".into()))
    }

    fn connection(
        &self,
        connection_id: Option<&str>,
        provider: &str,
        product: &str,
    ) -> ReferenceResult<Option<&ProviderConnectionProfile>> {
        let Some(connection_id) = connection_id
            .map(str::trim)
            .filter(|value| !value.is_empty())
        else {
            return Ok(None);
        };
        let profile = self.connections.get(connection_id).ok_or_else(|| {
            ReferenceError::Provider(format!(
                "Integration connection {connection_id} is not configured"
            ))
        })?;
        profile
            .require(provider, Some(product), "reference-catalog")
            .map_err(ReferenceError::Provider)?;
        Ok(Some(profile))
    }
}
