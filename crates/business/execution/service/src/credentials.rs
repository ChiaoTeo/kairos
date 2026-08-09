use kairos_workspace::workspace::Workspace;

#[derive(Clone, Debug, Default)]
pub struct WorkspaceCredential {
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
}

pub fn load_workspace_credential(
    workspace: &Workspace,
    provider: &str,
    requested_id: Option<&str>,
) -> Result<Option<WorkspaceCredential>, Box<dyn std::error::Error>> {
    let directory = workspace.child(&["credentials"])?;
    let Ok(entries) = std::fs::read_dir(directory) else {
        return Ok(None);
    };
    let provider = provider.trim().to_ascii_lowercase();
    for entry in entries {
        let path = entry?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("toml") {
            continue;
        }
        let text = std::fs::read_to_string(&path)?;
        let value: toml::Value = toml::from_str(&text)?;
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
        if requested_id.is_none() {
            let record_provider = table
                .get("broker")
                .or_else(|| table.get("provider"))
                .and_then(toml::Value::as_str)
                .unwrap_or_default()
                .to_ascii_lowercase();
            if record_provider != provider {
                continue;
            }
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
            api_key: credential_value(
                table,
                "api_key",
                &format!("KAIROS_CREDENTIAL_{prefix}_API_KEY"),
                match provider.as_str() {
                    "okx" | "okex" => "OKX_API_KEY",
                    _ => "BINANCE_API_KEY",
                },
            ),
            secret: credential_value(
                table,
                "api_secret",
                &format!("KAIROS_CREDENTIAL_{prefix}_API_SECRET"),
                match provider.as_str() {
                    "okx" | "okex" => "OKX_API_SECRET",
                    _ => "BINANCE_API_SECRET",
                },
            ),
            passphrase: credential_value(
                table,
                "passphrase",
                &format!("KAIROS_CREDENTIAL_{prefix}_PASSPHRASE"),
                "OKX_PASSPHRASE",
            ),
        }));
    }
    Ok(None)
}

fn credential_value(
    table: &toml::map::Map<String, toml::Value>,
    key: &str,
    namespaced_env: &str,
    conventional_env: &str,
) -> String {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .or_else(|| std::env::var(namespaced_env).ok())
        .or_else(|| std::env::var(conventional_env).ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::load_workspace_credential;
    use kairos_workspace::workspace::Workspace;

    #[test]
    fn loads_provider_credential_from_canonical_toml() {
        let directory = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(directory.path(), "execution").unwrap();
        let credentials = workspace
            .child(&["credentials", "binance-live.toml"])
            .unwrap();
        std::fs::write(
            credentials,
            r#"[credential]
id = "binance-live"
broker = "binance"
api_key = "key"
api_secret = "secret"
"#,
        )
        .unwrap();

        let value = load_workspace_credential(&workspace, "binance", None)
            .unwrap()
            .unwrap();
        assert_eq!(value.api_key, "key");
        assert_eq!(value.secret, "secret");
    }
}
