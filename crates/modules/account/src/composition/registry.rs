//! Account-owned binding and credential-file configuration persistence.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct AccountRegistry {
    #[serde(default)]
    pub accounts: Vec<AccountBindingRecord>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AccountBindingRecord {
    pub account_id: String,
    #[serde(default)]
    pub alias: String,
    pub provider: String,
    #[serde(default)]
    pub exchange: Option<String>,
    pub environment: String,
    #[serde(default)]
    pub remote_identity: Option<String>,
    #[serde(default)]
    pub permissions: BTreeMap<String, String>,
    pub segments: Vec<String>,
    /// Explicit provider product for each opaque Account segment key.
    #[serde(default)]
    pub segment_products: BTreeMap<String, String>,
    #[serde(default)]
    pub segment_trading_modes: BTreeMap<String, String>,
    pub account_model: Option<String>,
    #[serde(default)]
    pub credential_id: Option<String>,
    /// Named credentials attached to the account. `credential_id` is the
    /// default reference used when no named binding is selected.
    #[serde(default)]
    pub credentials: Vec<AccountCredentialBinding>,
    #[serde(default)]
    pub credential_role: Option<String>,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub initial_balances: Vec<String>,
    #[serde(default)]
    pub fee_rate: Option<String>,
    #[serde(default)]
    pub values: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, Eq, PartialEq)]
pub struct AccountCredentialBinding {
    pub name: String,
    pub credential_id: String,
    #[serde(default)]
    pub role: String,
}

impl AccountRegistry {
    pub fn load(path: impl Into<PathBuf>) -> Result<Self, String> {
        let path = path.into();
        if let Some(parent) = path.parent().filter(|value| value.is_dir()) {
            let has_toml = std::fs::read_dir(parent)
                .map_err(|error| error.to_string())?
                .filter_map(Result::ok)
                .any(|entry| {
                    entry.path().extension().and_then(|value| value.to_str()) == Some("toml")
                });
            if has_toml {
                let mut registry = Self::default();
                for entry in std::fs::read_dir(parent).map_err(|error| error.to_string())? {
                    let file = entry.map_err(|error| error.to_string())?.path();
                    if file.extension().and_then(|value| value.to_str()) == Some("toml") {
                        if let Some(record) = load_account_toml(&file)? {
                            registry.upsert_account(record);
                        }
                    }
                }
                return Ok(registry);
            }
        }
        Ok(Self::default())
    }

    pub fn save(&self, path: impl Into<PathBuf>) -> Result<(), String> {
        let path = path.into();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            let names = self
                .accounts
                .iter()
                .map(|value| format!("{}.toml", safe_file_name(&value.account_id)))
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
            for record in &self.accounts {
                let target = parent.join(format!("{}.toml", safe_file_name(&record.account_id)));
                write_atomic(&target, &account_toml(record)?)?;
            }
            if path.exists() {
                std::fs::remove_file(&path).map_err(|error| error.to_string())?;
            }
            return Ok(());
        }
        Ok(())
    }

    pub fn upsert_account(&mut self, record: AccountBindingRecord) {
        if let Some(existing) = self
            .accounts
            .iter_mut()
            .find(|v| v.account_id == record.account_id)
        {
            *existing = record;
        } else {
            self.accounts.push(record);
        }
        self.accounts
            .sort_by(|a, b| a.account_id.cmp(&b.account_id));
    }

    pub fn remove_account(&mut self, account_id: &str) -> bool {
        let before = self.accounts.len();
        self.accounts.retain(|v| v.account_id != account_id);
        before != self.accounts.len()
    }
}

impl AccountBindingRecord {
    pub fn product_for_segment(&self, segment_key: &str) -> Option<&str> {
        self.segment_products.get(segment_key).map(String::as_str)
    }
}

fn load_account_toml(path: &std::path::Path) -> Result<Option<AccountBindingRecord>, String> {
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let value: toml::Value = toml::from_str(&text)
        .map_err(|error| format!("invalid account TOML {}: {error}", path.display()))?;
    let Some(account) = value.get("account").and_then(toml::Value::as_table) else {
        return Ok(None);
    };
    let account_id = table_text(account, "id").unwrap_or_else(|| {
        path.file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_owned()
    });
    if account_id.trim().is_empty() {
        return Ok(None);
    }
    let provider = table_text(account, "broker")
        .or_else(|| table_text(account, "provider"))
        .unwrap_or_else(|| "paper".into());
    let environment = table_text(account, "environment").unwrap_or_else(|| "live".into());
    let exchange = table_text(account, "exchange");
    let default_segment = table_text(account, "default_segment");
    let mut segments = Vec::new();
    let mut segment_products = BTreeMap::new();
    let mut segment_trading_modes = BTreeMap::new();
    if let Some(segment_table) = value.get("segments").and_then(toml::Value::as_table) {
        for (key, segment) in segment_table {
            let mut product = segment
                .get("product_family")
                .and_then(toml::Value::as_str)
                .unwrap_or(key)
                .to_owned();
            let mut trading_mode = segment
                .get("trading_mode")
                .and_then(toml::Value::as_str)
                .map(str::to_owned);
            match product
                .trim()
                .to_ascii_lowercase()
                .replace('_', "-")
                .as_str()
            {
                "cross-margin" => {
                    product = "margin".into();
                    trading_mode.get_or_insert_with(|| "cross".into());
                }
                "isolated-margin" => {
                    product = "margin".into();
                    trading_mode.get_or_insert_with(|| "isolated".into());
                }
                _ => {}
            }
            segments.push(key.clone());
            segment_products.insert(key.clone(), product);
            if let Some(trading_mode) = trading_mode {
                segment_trading_modes.insert(key.clone(), trading_mode);
            }
        }
    }
    if segments.is_empty() {
        let segment = default_segment.clone().unwrap_or_else(|| "spot".into());
        segment_products.insert(segment.clone(), segment.clone());
        segments.push(segment);
    }
    let initial_balances = value
        .get("initial_balances")
        .and_then(toml::Value::as_table)
        .map(|balances| {
            balances
                .iter()
                .map(|(asset, amount)| format!("{}={}", asset, toml_value_text(amount)))
                .collect()
        })
        .unwrap_or_default();
    let mut credentials = Vec::new();
    if let Some(bindings) = value.get("credentials").and_then(toml::Value::as_table) {
        for (name, binding) in bindings {
            if let Some(reference) = binding
                .get("ref")
                .and_then(toml::Value::as_str)
                .filter(|value| !value.trim().is_empty())
            {
                credentials.push(AccountCredentialBinding {
                    name: name.clone(),
                    credential_id: reference.into(),
                    role: binding
                        .get("role")
                        .and_then(toml::Value::as_str)
                        .unwrap_or("readonly")
                        .to_owned(),
                });
            }
        }
    }
    let credential_id = table_text(account, "credential")
        .or_else(|| credentials.first().map(|value| value.credential_id.clone()));
    let credential_role = credentials.first().map(|value| value.role.clone());
    Ok(Some(AccountBindingRecord {
        account_id: account_id.clone(),
        alias: account_id,
        provider,
        exchange,
        environment,
        remote_identity: value
            .get("discovery")
            .and_then(toml::Value::as_table)
            .and_then(|table| table_text(table, "remote_identity")),
        permissions: value
            .get("permissions")
            .and_then(toml::Value::as_table)
            .map(|table| {
                table
                    .iter()
                    .map(|(key, value)| (key.clone(), toml_value_text(value)))
                    .collect()
            })
            .unwrap_or_default(),
        segments,
        segment_products,
        segment_trading_modes,
        account_model: account
            .get("model")
            .and_then(toml::Value::as_str)
            .map(str::to_owned),
        credential_id,
        credentials,
        credential_role,
        status: "configured".into(),
        initial_balances,
        fee_rate: table_text(account, "fee_rate"),
        values: account
            .iter()
            .filter_map(|(key, value)| value.as_str().map(|value| (key.clone(), value.into())))
            .collect(),
    }))
}

fn table_text(table: &toml::map::Map<String, toml::Value>, key: &str) -> Option<String> {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .map(str::to_owned)
}

fn toml_value_text(value: &toml::Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
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

fn toml_key(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
    {
        value.into()
    } else {
        toml_string(value)
    }
}

fn toml_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn write_atomic(path: &std::path::Path, contents: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let temporary = path.with_extension("tmp");
    std::fs::write(&temporary, contents).map_err(|error| error.to_string())?;
    std::fs::rename(temporary, path).map_err(|error| error.to_string())
}

fn account_toml(record: &AccountBindingRecord) -> Result<String, String> {
    let mut lines = vec![
        "[account]".into(),
        format!("id = {}", toml_string(&record.account_id)),
        format!("broker = {}", toml_string(&record.provider)),
        format!("environment = {}", toml_string(&record.environment)),
    ];
    if let Some(exchange) = &record.exchange {
        lines.push(format!("exchange = {}", toml_string(exchange)));
    }
    if !record.alias.is_empty() && record.alias != record.account_id {
        lines.push(format!("alias = {}", toml_string(&record.alias)));
    }
    if let Some(model) = &record.account_model {
        lines.push(format!("model = {}", toml_string(model)));
    }
    if let Some(fee_rate) = &record.fee_rate {
        lines.push(format!("fee_rate = {}", toml_string(fee_rate)));
    }
    if let Some(credential_id) = &record.credential_id {
        if record.credentials.is_empty() {
            lines.push(format!("credential = {}", toml_string(credential_id)));
        }
    }
    for (key, value) in &record.values {
        if !matches!(
            key.as_str(),
            "id" | "broker"
                | "provider"
                | "environment"
                | "exchange"
                | "alias"
                | "model"
                | "fee_rate"
        ) {
            lines.push(format!("{} = {}", toml_key(key), toml_string(value)));
        }
    }
    if let Some(identity) = &record.remote_identity {
        lines.extend([
            String::new(),
            "[discovery]".into(),
            format!("remote_identity = {}", toml_string(identity)),
        ]);
    }
    if !record.permissions.is_empty() {
        lines.push(String::new());
        lines.push("[permissions]".into());
        for (key, value) in &record.permissions {
            lines.push(format!("{} = {}", toml_key(key), toml_string(value)));
        }
    }
    for segment in &record.segments {
        let provider_product = record
            .product_for_segment(segment)
            .ok_or_else(|| format!("account segment {segment} has no explicit provider product"))?;
        lines.extend([
            String::new(),
            format!("[segments.{}]", toml_key(segment)),
            format!("product_family = {}", toml_string(provider_product)),
        ]);
        if let Some(trading_mode) = record.segment_trading_modes.get(segment) {
            lines.push(format!("trading_mode = {}", toml_string(trading_mode)));
        }
    }
    if !record.initial_balances.is_empty() {
        lines.extend([String::new(), "[initial_balances]".into()]);
        for value in &record.initial_balances {
            if let Some((asset, amount)) = value.split_once('=') {
                lines.push(format!("{} = {}", toml_key(asset), toml_string(amount)));
            }
        }
    }
    for binding in &record.credentials {
        lines.extend([
            String::new(),
            format!("[credentials.{}]", toml_key(&binding.name)),
            format!("ref = {}", toml_string(&binding.credential_id)),
            format!("role = {}", toml_string(&binding.role)),
        ]);
    }
    Ok(format!("{}\n", lines.join("\n")))
}
