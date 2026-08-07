//! Workspace credential resolution for concrete integration composition.

use std::path::Path;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct WorkspaceCredential {
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
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
        let prefix: String = id
            .chars()
            .map(|value| {
                if value.is_ascii_alphanumeric() {
                    value.to_ascii_uppercase()
                } else {
                    '_'
                }
            })
            .collect();
        return Ok(Some(WorkspaceCredential {
            api_key: value_or_env(
                table,
                "api_key",
                &format!("KAIROS_CREDENTIAL_{prefix}_API_KEY"),
                "BINANCE_API_KEY",
            ),
            secret: value_or_env(
                table,
                "api_secret",
                &format!("KAIROS_CREDENTIAL_{prefix}_API_SECRET"),
                "BINANCE_API_SECRET",
            ),
            passphrase: value_or_env(
                table,
                "passphrase",
                &format!("KAIROS_CREDENTIAL_{prefix}_PASSPHRASE"),
                "OKX_PASSPHRASE",
            ),
        }));
    }
    Ok(None)
}

fn value_or_env(
    table: &toml::map::Map<String, toml::Value>,
    key: &str,
    namespaced: &str,
    conventional: &str,
) -> String {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .or_else(|| std::env::var(namespaced).ok())
        .or_else(|| std::env::var(conventional).ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::load_workspace_credential;

    #[test]
    fn loads_workspace_credential_by_id_and_resolves_external_secret() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("binance-equity-readonly.toml"),
            r#"[credential]
id = "binance-equity-readonly"
provider = "binance"
api_key = "stored-key"
"#,
        )
        .unwrap();
        let name = "KAIROS_CREDENTIAL_BINANCE_EQUITY_READONLY_API_SECRET";
        std::env::set_var(name, "external-secret");
        let credential =
            load_workspace_credential(directory.path(), "binance", Some("binance-equity-readonly"))
                .unwrap()
                .unwrap();
        std::env::remove_var(name);

        assert_eq!(credential.api_key, "stored-key");
        assert_eq!(credential.secret, "external-secret");
    }
}
