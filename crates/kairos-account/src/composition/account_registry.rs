//! Workspace-owned account and credential registry persistence.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct AccountRegistry {
    #[serde(default)]
    pub accounts: Vec<AccountBindingRecord>,
    #[serde(default)]
    pub credentials: Vec<CredentialRecord>,
    #[serde(default)]
    pub locks: Vec<TradeLockRecord>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AccountBindingRecord {
    pub account_id: String,
    #[serde(default)]
    pub alias: String,
    pub provider: String,
    #[serde(default)]
    pub venue: Option<String>,
    pub environment: String,
    #[serde(default)]
    pub remote_identity: Option<String>,
    #[serde(default)]
    pub permissions: BTreeMap<String, String>,
    pub segments: Vec<String>,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CredentialRecord {
    pub credential_id: String,
    pub provider: String,
    pub role: String,
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CredentialStore {
    #[serde(default)]
    pub credentials: Vec<CredentialRecord>,
}

impl CredentialStore {
    /// Load the workspace-owned credential catalog from per-record TOML files.
    pub fn load(path: impl Into<PathBuf>) -> Result<Self, String> {
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

    pub fn save(&self, path: impl Into<PathBuf>) -> Result<(), String> {
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
            return Ok(());
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

impl CredentialRecord {
    /// Resolve a secret without requiring it to be persisted in workspace
    /// configuration. Explicit values are the canonical persisted secret
    /// fields; environment variables provide runtime secret injection.
    pub fn api_key_value(&self) -> Option<String> {
        self.resolve("API_KEY", self.provider_env("API_KEY"), &self.api_key)
    }

    pub fn secret_value(&self) -> Option<String> {
        self.resolve("API_SECRET", self.provider_env("API_SECRET"), &self.secret)
    }

    pub fn passphrase_value(&self) -> Option<String> {
        self.resolve("PASSPHRASE", "OKX_PASSPHRASE", &self.passphrase)
    }

    fn provider_env(&self, field: &str) -> &'static str {
        match (self.provider.to_ascii_lowercase().as_str(), field) {
            ("okx" | "okex", "API_KEY") => "OKX_API_KEY",
            ("okx" | "okex", "API_SECRET") => "OKX_API_SECRET",
            (_, "API_SECRET") => "BINANCE_API_SECRET",
            _ => "BINANCE_API_KEY",
        }
    }

    fn resolve(&self, field: &str, conventional: &str, stored: &str) -> Option<String> {
        if !stored.trim().is_empty() {
            return Some(stored.to_owned());
        }
        let prefix: String = self
            .credential_id
            .chars()
            .map(|value| {
                if value.is_ascii_alphanumeric() {
                    value.to_ascii_uppercase()
                } else {
                    '_'
                }
            })
            .collect();
        std::env::var(format!("KAIROS_CREDENTIAL_{prefix}_{field}"))
            .ok()
            .or_else(|| std::env::var(conventional).ok())
            .filter(|value| !value.trim().is_empty())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TradeLockRecord {
    pub account_id: String,
    pub owner: String,
    pub acquired_at_unix_nanos: u64,
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
                    if file.file_name().and_then(|value| value.to_str()) == Some("locks.toml") {
                        load_locks(&file, &mut registry)?;
                    } else if file.extension().and_then(|value| value.to_str()) == Some("toml") {
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
                    && name != "locks.toml"
                    && !names.contains(name)
                {
                    std::fs::remove_file(file).map_err(|error| error.to_string())?;
                }
            }
            for record in &self.accounts {
                let target = parent.join(format!("{}.toml", safe_file_name(&record.account_id)));
                write_atomic(&target, &account_toml(record))?;
            }
            write_atomic(&parent.join("locks.toml"), &locks_toml(&self.locks))?;
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

    pub fn upsert_credential(&mut self, record: CredentialRecord) {
        if let Some(existing) = self
            .credentials
            .iter_mut()
            .find(|v| v.credential_id == record.credential_id)
        {
            *existing = record;
        } else {
            self.credentials.push(record);
        }
        self.credentials
            .sort_by(|a, b| a.credential_id.cmp(&b.credential_id));
    }

    pub fn remove_credential(&mut self, credential_id: &str) -> bool {
        let before = self.credentials.len();
        self.credentials
            .retain(|v| v.credential_id != credential_id);
        before != self.credentials.len()
    }

    pub fn acquire_lock(&mut self, account_id: &str, owner: &str) -> Result<(), String> {
        if let Some(existing) = self.locks.iter().find(|lock| lock.account_id == account_id) {
            return Err(format!("account is already locked by {}", existing.owner));
        }
        self.locks.push(TradeLockRecord {
            account_id: account_id.into(),
            owner: owner.into(),
            acquired_at_unix_nanos: now_nanos(),
        });
        Ok(())
    }

    pub fn release_lock(&mut self, account_id: &str, owner: Option<&str>) -> bool {
        let before = self.locks.len();
        self.locks.retain(|lock| {
            !(lock.account_id == account_id && owner.is_none_or(|value| value == lock.owner))
        });
        before != self.locks.len()
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
    let venue = table_text(account, "venue");
    let default_segment = table_text(account, "default_segment");
    let mut segments = Vec::new();
    if let Some(segment_table) = value.get("segments").and_then(toml::Value::as_table) {
        for (key, segment) in segment_table {
            let product = segment
                .get("product_family")
                .and_then(toml::Value::as_str)
                .unwrap_or(key)
                .to_owned();
            segments.push(product);
        }
    }
    if segments.is_empty() {
        segments.push(default_segment.clone().unwrap_or_else(|| "spot".into()));
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
        venue,
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

fn load_credential_toml(path: &std::path::Path) -> Result<Option<CredentialRecord>, String> {
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let value: toml::Value = toml::from_str(&text)
        .map_err(|error| format!("invalid credential TOML {}: {error}", path.display()))?;
    let table = value
        .get("credential")
        .and_then(toml::Value::as_table)
        .unwrap_or_else(|| value.as_table().expect("TOML root is a table"));
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

fn account_toml(record: &AccountBindingRecord) -> String {
    let mut lines = vec![
        "[account]".into(),
        format!("id = {}", toml_string(&record.account_id)),
        format!("broker = {}", toml_string(&record.provider)),
        format!("environment = {}", toml_string(&record.environment)),
    ];
    if let Some(venue) = &record.venue {
        lines.push(format!("venue = {}", toml_string(venue)));
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
            "id" | "broker" | "provider" | "environment" | "venue" | "alias" | "model" | "fee_rate"
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
        lines.extend([
            String::new(),
            format!("[segments.{}]", toml_key(segment)),
            format!("product_family = {}", toml_string(segment)),
        ]);
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
    format!("{}\n", lines.join("\n"))
}

fn locks_toml(locks: &[TradeLockRecord]) -> String {
    let mut lines = Vec::new();
    for lock in locks {
        lines.extend([
            format!("[locks.{}]", toml_key(&lock.account_id)),
            format!("owner = {}", toml_string(&lock.owner)),
            format!("acquired_at_unix_nanos = {}", lock.acquired_at_unix_nanos),
            String::new(),
        ]);
    }
    lines.join("\n")
}

fn load_locks(path: &std::path::Path, registry: &mut AccountRegistry) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let value: toml::Value = toml::from_str(&text).map_err(|error| error.to_string())?;
    if let Some(table) = value.get("locks").and_then(toml::Value::as_table) {
        for (account_id, lock) in table {
            let Some(lock) = lock.as_table() else {
                continue;
            };
            if let Some(owner) = table_text(lock, "owner") {
                registry.locks.push(TradeLockRecord {
                    account_id: account_id.clone(),
                    owner,
                    acquired_at_unix_nanos: lock
                        .get("acquired_at_unix_nanos")
                        .and_then(toml::Value::as_integer)
                        .unwrap_or_default() as u64,
                });
            }
        }
    }
    Ok(())
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
