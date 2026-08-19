//! Workspace credential resolution for concrete integration composition.

use std::path::Path;

use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default)]
pub struct WorkspaceCredential {
    pub api_key: String,
    pub secret: SecretString,
    pub passphrase: String,
}

impl WorkspaceCredential {
    pub fn secret_value(&self) -> &str {
        self.secret.expose_secret()
    }
}

pub fn load_workspace_credential(
    credentials_root: &Path,
    provider: &str,
    requested_id: Option<&str>,
) -> Result<Option<WorkspaceCredential>, String> {
    let provider = provider.trim().to_ascii_lowercase();
    let entries = std::fs::read_dir(credentials_root).ok();
    for entry in entries.into_iter().flatten() {
        let path = entry.map_err(|error| error.to_string())?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("toml") {
            continue;
        }
        let text = std::fs::read_to_string(&path).map_err(|error| error.to_string())?;
        let value: toml::Value = toml::from_str(&text).map_err(|error| error.to_string())?;
        let table = value
            .get("credential")
            .and_then(toml::Value::as_table)
            .or_else(|| value.as_table())
            .ok_or_else(|| format!("credential TOML root is not a table: {}", path.display()))?;
        let id = table
            .get("id")
            .and_then(toml::Value::as_str)
            .or_else(|| path.file_stem().and_then(|value| value.to_str()))
            .unwrap_or_default();
        let record_provider = table
            .get("provider")
            .or_else(|| table.get("broker"))
            .and_then(toml::Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        if requested_id.is_some_and(|requested| requested != id) {
            continue;
        }
        if record_provider != provider {
            continue;
        }
        let api_key = resolve_credential_value(
            &provider,
            id,
            "API_KEY",
            &value_from_table(table, "api_key"),
        )?
        .unwrap_or_default();
        let secret = resolve_credential_value(
            &provider,
            id,
            "API_SECRET",
            &value_from_table(table, "api_secret"),
        )?
        .unwrap_or_default();
        let passphrase = resolve_credential_value(
            &provider,
            id,
            "PASSPHRASE",
            &value_from_table(table, "passphrase"),
        )?
        .unwrap_or_default();
        return Ok(Some(WorkspaceCredential {
            api_key,
            secret: SecretString::from(secret),
            passphrase,
        }));
    }
    let id = requested_id.unwrap_or(provider.as_str());
    let api_key = resolve_credential_value(&provider, id, "API_KEY", "")?.unwrap_or_default();
    let secret = resolve_credential_value(&provider, id, "API_SECRET", "")?.unwrap_or_default();
    let passphrase = resolve_credential_value(&provider, id, "PASSPHRASE", "")?.unwrap_or_default();
    if api_key.is_empty() && secret.is_empty() && passphrase.is_empty() {
        Ok(None)
    } else {
        Ok(Some(WorkspaceCredential {
            api_key,
            secret: SecretString::from(secret),
            passphrase,
        }))
    }
}

/// Resolve one credential value using provider-owned environment conventions.
/// Unknown providers never inherit another provider's variables.
pub fn resolve_credential_value(
    provider: &str,
    credential_id: &str,
    field: &str,
    stored: &str,
) -> Result<Option<String>, String> {
    if !stored.trim().is_empty() {
        return Ok(Some(stored.to_owned()));
    }
    let provider = provider.trim().to_ascii_lowercase();
    let conventional = match (provider.as_str(), field) {
        ("okx" | "okex", "API_KEY") => Some("OKX_API_KEY"),
        ("okx" | "okex", "API_SECRET") => Some("OKX_API_SECRET"),
        ("okx" | "okex", "PASSPHRASE") => Some("OKX_PASSPHRASE"),
        ("binance", "API_KEY") => Some("BINANCE_API_KEY"),
        ("binance", "API_SECRET") => Some("BINANCE_API_SECRET"),
        ("massive", "API_KEY") => Some("MASSIVE_API_KEY"),
        ("binance" | "massive", _) => None,
        ("ibkr" | "hyperliquid" | "simulated" | "paper", _) => None,
        _ => return Err(format!("unsupported credential provider: {provider}")),
    };
    let prefix: String = credential_id
        .chars()
        .map(|value| {
            if value.is_ascii_alphanumeric() {
                value.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect();
    Ok(std::env::var(format!("KAIROS_CREDENTIAL_{prefix}_{field}"))
        .ok()
        .or_else(|| conventional.and_then(|name| std::env::var(name).ok()))
        .filter(|value| !value.trim().is_empty()))
}

#[derive(Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct CredentialRecord {
    pub credential_id: String,
    pub provider: String,
    pub role: String,
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
}

impl std::fmt::Debug for CredentialRecord {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CredentialRecord")
            .field("credential_id", &self.credential_id)
            .field("provider", &self.provider)
            .field("role", &self.role)
            .field("api_key", &"[REDACTED]")
            .field("secret", &"[REDACTED]")
            .field("passphrase", &"[REDACTED]")
            .finish()
    }
}

impl CredentialRecord {
    pub fn api_key_value(&self) -> Option<String> {
        self.resolve("API_KEY", &self.api_key)
    }

    pub fn secret_value(&self) -> Option<String> {
        self.resolve("API_SECRET", &self.secret)
    }

    pub fn passphrase_value(&self) -> Option<String> {
        self.resolve("PASSPHRASE", &self.passphrase)
    }

    fn resolve(&self, field: &str, stored: &str) -> Option<String> {
        resolve_credential_value(&self.provider, &self.credential_id, field, stored)
            .ok()
            .flatten()
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CredentialStore {
    #[serde(default)]
    pub credentials: Vec<CredentialRecord>,
}

impl CredentialStore {
    pub fn load(path: impl Into<std::path::PathBuf>) -> Result<Self, String> {
        let path = path.into();
        if let Some(parent) = path.parent().filter(|value| value.is_dir()) {
            let mut values = Vec::new();
            for entry in std::fs::read_dir(parent).map_err(|error| error.to_string())? {
                let file = entry.map_err(|error| error.to_string())?.path();
                if file.extension().and_then(|value| value.to_str()) == Some("toml") {
                    if let Some(record) = load_credential_toml(&file)? {
                        values.push(record);
                    }
                }
            }
            if !values.is_empty() {
                values.sort_by(|left, right| left.credential_id.cmp(&right.credential_id));
                return Ok(Self {
                    credentials: values,
                });
            }
        }
        Ok(Self::default())
    }

    pub fn save(&self, path: impl Into<std::path::PathBuf>) -> Result<(), String> {
        let path = path.into();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            let names = self
                .credentials
                .iter()
                .map(|value| format!("{}.toml", safe_file_name(&value.credential_id)))
                .collect::<std::collections::BTreeSet<_>>();
            for entry in std::fs::read_dir(parent).map_err(|error| error.to_string())? {
                let file = entry.map_err(|error| error.to_string())?.path();
                let name = file
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or_default();
                if file.extension().and_then(|value| value.to_str()) == Some("toml")
                    && !names.contains(name)
                {
                    std::fs::remove_file(file).map_err(|error| error.to_string())?;
                }
            }
            for record in &self.credentials {
                let target = parent.join(format!("{}.toml", safe_file_name(&record.credential_id)));
                write_atomic(&target, &credential_toml(record))?;
            }
            if path.exists() {
                std::fs::remove_file(&path).map_err(|error| error.to_string())?;
            }
        }
        Ok(())
    }

    pub fn upsert(&mut self, record: CredentialRecord) {
        if let Some(existing) = self
            .credentials
            .iter_mut()
            .find(|value| value.credential_id == record.credential_id)
        {
            *existing = record;
        } else {
            self.credentials.push(record);
        }
        self.credentials
            .sort_by(|left, right| left.credential_id.cmp(&right.credential_id));
    }

    pub fn remove(&mut self, credential_id: &str) -> bool {
        let before = self.credentials.len();
        self.credentials
            .retain(|value| value.credential_id != credential_id);
        before != self.credentials.len()
    }
}

fn load_credential_toml(path: &Path) -> Result<Option<CredentialRecord>, String> {
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let value: toml::Value = toml::from_str(&text)
        .map_err(|error| format!("invalid credential TOML {}: {error}", path.display()))?;
    let table = value
        .get("credential")
        .and_then(toml::Value::as_table)
        .or_else(|| value.as_table())
        .ok_or_else(|| format!("credential TOML root is not a table: {}", path.display()))?;
    let credential_id = table_text(table, "id").unwrap_or_else(|| {
        path.file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_owned()
    });
    if credential_id.trim().is_empty() {
        return Ok(None);
    }
    Ok(Some(CredentialRecord {
        credential_id,
        provider: table_text(table, "broker")
            .or_else(|| table_text(table, "provider"))
            .unwrap_or_else(|| "unknown".into()),
        role: table_text(table, "role").unwrap_or_else(|| "readonly".into()),
        api_key: table_text(table, "api_key").unwrap_or_default(),
        secret: table_text(table, "api_secret").unwrap_or_default(),
        passphrase: table_text(table, "passphrase").unwrap_or_default(),
    }))
}

fn table_text(table: &toml::map::Map<String, toml::Value>, key: &str) -> Option<String> {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .map(str::to_owned)
}

fn safe_file_name(value: &str) -> String {
    let name: String = value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect();
    if name.is_empty() {
        "unnamed".into()
    } else {
        name
    }
}

fn toml_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn write_atomic(path: &Path, contents: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, contents).map_err(|error| error.to_string())?;
    std::fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn credential_toml(record: &CredentialRecord) -> String {
    format!(
        "[credential]\nid = {}\nbroker = {}\nrole = {}\napi_key = {}\napi_secret = {}\npassphrase = {}\n",
        toml_string(&record.credential_id),
        toml_string(&record.provider),
        toml_string(&record.role),
        toml_string(&record.api_key),
        toml_string(&record.secret),
        toml_string(&record.passphrase),
    )
}

fn value_from_table(table: &toml::map::Map<String, toml::Value>, key: &str) -> String {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::{CredentialRecord, load_workspace_credential};

    #[test]
    fn loads_workspace_credential_by_id_from_toml_only() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("binance-spot-readonly.toml"),
            r#"[credential]
id = "binance-spot-readonly"
provider = "binance"
api_key = "stored-key"
"#,
        )
        .unwrap();
        let credential =
            load_workspace_credential(directory.path(), "binance", Some("binance-spot-readonly"))
                .unwrap()
                .unwrap();

        assert_eq!(credential.api_key, "stored-key");
        assert!(credential.secret_value().is_empty());
    }

    #[test]
    fn secret_is_not_exposed_by_debug_formatting() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("binance.toml"),
            r#"[credential]
provider = "binance"
api_secret = "do-not-log-me"
"#,
        )
        .unwrap();

        let credential = load_workspace_credential(directory.path(), "binance", None)
            .unwrap()
            .unwrap();
        assert_eq!(credential.secret_value(), "do-not-log-me");
        assert!(!format!("{credential:?}").contains("do-not-log-me"));
    }

    #[test]
    fn credential_record_redacts_secrets_and_unknown_provider_has_no_fallback() {
        let record = CredentialRecord {
            credential_id: "future".into(),
            provider: "future-provider".into(),
            role: "trading".into(),
            api_key: String::new(),
            secret: "secret-value".into(),
            passphrase: "passphrase-value".into(),
        };
        let debug = format!("{record:?}");
        assert!(!debug.contains("secret-value"));
        assert!(!debug.contains("passphrase-value"));
        assert_eq!(record.api_key_value(), None);
    }
}
