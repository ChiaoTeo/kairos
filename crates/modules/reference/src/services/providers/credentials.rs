use kairos_conflux::BinanceCredential;
use kairos_credentials::CredentialStore;

use crate::domain::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Default)]
pub(crate) struct ReferenceCredentialResolver {
    binance: std::collections::BTreeMap<String, BinanceCredential>,
    massive: std::collections::BTreeMap<String, String>,
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
        binding: Option<&str>,
    ) -> ReferenceResult<Option<BinanceCredential>> {
        let Some(binding) = binding.map(str::trim).filter(|value| !value.is_empty()) else {
            return Ok(None);
        };
        self.binance
            .get(&binding.to_ascii_lowercase())
            .cloned()
            .map(Some)
            .ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "Reference credential binding {binding} is not available for Binance"
                ))
            })
    }

    pub(super) fn massive(&self, binding: Option<&str>) -> ReferenceResult<Option<String>> {
        let Some(binding) = binding.map(str::trim).filter(|value| !value.is_empty()) else {
            return Ok(None);
        };
        self.massive
            .get(&binding.to_ascii_lowercase())
            .cloned()
            .map(Some)
            .ok_or_else(|| {
                ReferenceError::Provider(format!(
                    "Reference credential binding {binding} is not available for Massive"
                ))
            })
    }
}
