//! Workspace credential resolution for concrete integration composition.

use std::path::Path;

use secrecy::{ExposeSecret, SecretString};

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
    let Ok(entries) = std::fs::read_dir(credentials_root) else {
        return Ok(None);
    };
    let provider = provider.trim().to_ascii_lowercase();
    for entry in entries {
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
        if requested_id.is_some_and(|requested| requested != id) {
            continue;
        }
        if requested_id.is_none()
            && table
                .get("provider")
                .or_else(|| table.get("broker"))
                .and_then(toml::Value::as_str)
                .unwrap_or_default()
                .to_ascii_lowercase()
                != provider
        {
            continue;
        }
        return Ok(Some(WorkspaceCredential {
            api_key: value_from_table(table, "api_key"),
            secret: SecretString::from(value_from_table(table, "api_secret")),
            passphrase: value_from_table(table, "passphrase"),
        }));
    }
    Ok(None)
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
    use super::load_workspace_credential;

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
}
