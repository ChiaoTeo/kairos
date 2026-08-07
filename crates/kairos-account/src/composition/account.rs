use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;

use crate::application::account::protocol::{
    AccountMarketProfileSource, AccountSnapshotSource, AccountStreamSource,
};
use crate::application::account::{
    AccountApplication, AccountEvent, AccountFill, AccountMarketProfile,
    AccountMarketProfileRequest, AccountModel, AccountSegment, AccountSnapshot, AccountStatus,
    Balance, DecimalValue, ExternalAccountIdentity, OpenOrder, OrderEvent, OrderStatus, Position,
};
use crate::composition::{empty_snapshot, InMemoryAccountSource, JsonAccountStore};
use crate::domain::{MarginMode, PositionMode};
use kairos_integration::application::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
    ConnectionSpec, EarnConnection, ExternalAccountCredentialProfile, ExternalMarketProfileRequest,
    TransferConnection,
};
use kairos_integration::domain::{
    AccessScope, ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalMarginMode,
    ExternalOrderStatus, ExternalPositionMode, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::Integration;

#[derive(Clone, Debug)]
pub struct AccountOptions {
    pub provider: String,
    pub product: String,
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
    pub base_url: String,
    pub account_id: String,
    pub segment: String,
    pub environment: String,
    pub account_model: Option<String>,
    pub initial_balances: Vec<String>,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

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

pub struct AccountComposition {
    pub application: AccountApplication,
    pub integration: Integration,
    pub provider: String,
    pub product: ProductFamily,
}

pub struct IntegrationAccountSource<C> {
    connection: C,
}

impl<C> IntegrationAccountSource<C> {
    pub fn new(connection: C) -> Self {
        Self { connection }
    }
}

impl<C: AccountReadConnection + Send> AccountSnapshotSource for IntegrationAccountSource<C> {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        let external = ExternalAccountSegment {
            identity: kairos_integration::domain::account::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
            segment_key: segment.segment_key.clone(),
            environment: segment.environment.clone(),
            account_model: segment.account_model.clone(),
        };
        self.connection
            .fetch_account(&external)
            .map(map_snapshot)
            .map_err(|error| error.to_string())
    }
}

/// Routes each configured account segment to its own integration connection.
/// A single venue account may expose spot, margin, and derivatives through
/// different endpoints; reusing the first connection for every segment would
/// silently read the wrong product.
pub struct MultiIntegrationAccountSource {
    connections: std::collections::BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
}

impl MultiIntegrationAccountSource {
    pub fn new(
        connections: std::collections::BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
    ) -> Self {
        Self { connections }
    }
}

impl AccountSnapshotSource for MultiIntegrationAccountSource {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        let connection = self
            .connections
            .get_mut(&segment.segment_key)
            .ok_or_else(|| format!("account segment is not configured: {}", segment.segment_key))?;
        let external = ExternalAccountSegment {
            identity: kairos_integration::domain::account::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
            segment_key: segment.segment_key.clone(),
            environment: segment.environment.clone(),
            account_model: segment.account_model.clone(),
        };
        connection
            .fetch_account(&external)
            .map(map_snapshot)
            .map_err(|error| error.to_string())
    }
}

pub struct IntegrationAccountProfileSource<C> {
    connection: C,
}

impl<C> IntegrationAccountProfileSource<C> {
    pub fn new(connection: C) -> Self {
        Self { connection }
    }
}

impl<C: AccountMarketProfileConnection + Send> AccountMarketProfileSource
    for IntegrationAccountProfileSource<C>
{
    fn fetch_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let external = ExternalMarketProfileRequest {
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            market_id: request.market_id.clone(),
            source_symbol: request.source_symbol.clone(),
        };
        self.connection
            .fetch_market_profile(&external)
            .map(map_profile)
            .map_err(|error| error.to_string())
    }
}

pub struct MultiIntegrationAccountProfileSource {
    connections: std::collections::BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
}

impl MultiIntegrationAccountProfileSource {
    pub fn new(
        connections: std::collections::BTreeMap<
            String,
            Box<dyn AccountMarketProfileConnection + Send>,
        >,
    ) -> Self {
        Self { connections }
    }
}

impl AccountMarketProfileSource for MultiIntegrationAccountProfileSource {
    fn fetch_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let connection = self
            .connections
            .get_mut(&request.segment_key)
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        let external = ExternalMarketProfileRequest {
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            market_id: request.market_id.clone(),
            source_symbol: request.source_symbol.clone(),
        };
        connection
            .fetch_market_profile(&external)
            .map(map_profile)
            .map_err(|error| error.to_string())
    }
}

pub struct IntegrationAccountStreamAdapter {
    stream: BufferedIntegrationAccountStream,
}

impl IntegrationAccountStreamAdapter {
    pub fn new(stream: BufferedIntegrationAccountStream) -> Self {
        Self { stream }
    }
}

impl AccountStreamSource for IntegrationAccountStreamAdapter {
    fn next_event(&mut self) -> Result<Option<AccountEvent>, String> {
        self.stream.next_event().map(|event| event.map(map_event))
    }
}

fn decimal(value: ExternalDecimal) -> DecimalValue {
    DecimalValue::new(value.mantissa, value.scale)
}

fn map_balance(value: ExternalBalance) -> Balance {
    Balance {
        asset_id: value.asset_id,
        asset_code: value.asset_code,
        total: decimal(value.total),
        available: value.available.map(decimal),
        locked: value.locked.map(decimal),
        borrowed: value.borrowed.map(decimal),
        interest: value.interest.map(decimal),
    }
}

fn map_position(value: kairos_integration::domain::ExternalPosition) -> Position {
    Position {
        instrument_id: value.instrument_id,
        market_id: value.market_id,
        quantity: decimal(value.quantity),
        average_price: value.average_price.map(decimal),
        mark_price: value.mark_price.map(decimal),
        unrealized_pnl: value.unrealized_pnl.map(decimal),
        realized_pnl: value.realized_pnl.map(decimal),
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    }
}

fn map_snapshot(value: kairos_integration::domain::ExternalAccountSnapshot) -> AccountSnapshot {
    AccountSnapshot {
        segment_key: value.segment_key,
        balances: value.balances.into_iter().map(map_balance).collect(),
        collateral: value.collateral.into_iter().map(map_balance).collect(),
        positions: value.positions.into_iter().map(map_position).collect(),
        open_orders: value.open_orders.into_iter().map(map_open_order).collect(),
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(decimal),
        initial_equity: value.initial_equity.map(decimal),
        net_profit: value.net_profit.map(decimal),
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.map(map_margin),
        position_mode: value.position_mode.map(map_position_mode),
        partial: value.partial,
    }
}

fn map_open_order(value: kairos_integration::domain::ExternalOpenOrder) -> OpenOrder {
    OpenOrder {
        order_id: value.order_id,
        venue_order_id: value.venue_order_id,
        instrument_id: value.instrument_id,
        side: value.side,
        quantity: decimal(value.quantity),
        filled_quantity: decimal(value.filled_quantity),
        status: value.status,
    }
}

fn map_status(value: ExternalAccountStatus) -> AccountStatus {
    match value {
        ExternalAccountStatus::Unknown => AccountStatus::Unknown,
        ExternalAccountStatus::Ready => AccountStatus::Ready,
        ExternalAccountStatus::Reconciling => AccountStatus::Reconciling,
        ExternalAccountStatus::TypeMismatch => AccountStatus::TypeMismatch,
        ExternalAccountStatus::Suspended => AccountStatus::Suspended,
        ExternalAccountStatus::Unavailable => AccountStatus::Unavailable,
    }
}
fn map_model(value: ExternalAccountModel) -> AccountModel {
    match value {
        ExternalAccountModel::NoMargin => AccountModel::NoMargin,
        ExternalAccountModel::Margin => AccountModel::Margin,
        ExternalAccountModel::Contract => AccountModel::Contract,
        ExternalAccountModel::ContractUnified => AccountModel::ContractUnified,
        ExternalAccountModel::Unified => AccountModel::Unified,
        ExternalAccountModel::PortfolioMargin => AccountModel::PortfolioMargin,
    }
}
fn map_margin(value: ExternalMarginMode) -> MarginMode {
    match value {
        ExternalMarginMode::Cross => MarginMode::Cross,
        ExternalMarginMode::Isolated => MarginMode::Isolated,
    }
}
fn map_position_mode(value: ExternalPositionMode) -> PositionMode {
    match value {
        ExternalPositionMode::OneWay => PositionMode::OneWay,
        ExternalPositionMode::Hedge => PositionMode::Hedge,
    }
}
fn map_order_status(value: ExternalOrderStatus) -> OrderStatus {
    match value {
        ExternalOrderStatus::Acknowledged => OrderStatus::Acknowledged,
        ExternalOrderStatus::PartiallyFilled => OrderStatus::PartiallyFilled,
        ExternalOrderStatus::Filled => OrderStatus::Filled,
        ExternalOrderStatus::Canceled => OrderStatus::Canceled,
        ExternalOrderStatus::Rejected => OrderStatus::Rejected,
        ExternalOrderStatus::Expired => OrderStatus::Expired,
        ExternalOrderStatus::Unknown => OrderStatus::Unknown,
    }
}
fn map_event(value: ExternalAccountEvent) -> AccountEvent {
    match value {
        ExternalAccountEvent::Batch(values) => {
            AccountEvent::Batch(values.into_iter().map(map_event).collect())
        }
        ExternalAccountEvent::Snapshot(value) => AccountEvent::Snapshot(map_snapshot(value)),
        ExternalAccountEvent::Order(value) => AccountEvent::Order(OrderEvent {
            order_id: value.order_id,
            status: map_order_status(value.status),
            venue_order_id: value.venue_order_id,
            filled_quantity: value.filled_quantity.map(decimal),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
            reason: value.reason,
        }),
        ExternalAccountEvent::Fill(value) => AccountEvent::Fill(AccountFill {
            fill_id: Some(value.fill_id),
            order_id: Some(value.order_id),
            segment_key: value.segment_key,
            instrument_id: value.instrument_id,
            quantity: decimal(value.quantity),
            price: decimal(value.price),
            side: if value.side.eq_ignore_ascii_case("sell") {
                crate::domain::FillSide::Sell
            } else {
                crate::domain::FillSide::Buy
            },
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: value.fee_asset,
            fee_amount: value.fee_amount.map(decimal),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
        }),
    }
}
fn map_profile(
    value: kairos_integration::application::ExternalMarketProfile,
) -> AccountMarketProfile {
    AccountMarketProfile {
        account_id: value.account_id,
        segment_key: value.segment_key,
        market_id: value.market_id,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode,
        position_mode: value.position_mode,
        maker_fee: value.maker_fee.map(decimal),
        taker_fee: value.taker_fee.map(decimal),
        fee_currency: value.fee_currency,
        fee_discount: value.fee_discount.map(decimal),
        fee_tier: value.fee_tier,
        source: value.source,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    }
}

pub fn compose_account_application(
    options: &AccountOptions,
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    compose_account_application_for_segments(options, &[options.segment.clone()], state)
}

/// Compose one account actor with every configured segment for the account.
///
/// A provider connection remains the integration-owned source, while the
/// account actor owns the complete set of segment state.  Keeping this
/// function at the account composition boundary lets CLI and server use the
/// same multi-segment path without making integration depend on account
/// configuration.
pub fn compose_account_application_for_segments(
    options: &AccountOptions,
    segments: &[String],
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    if segments.is_empty() {
        return Err("at least one account segment is required".into());
    }
    let provider = normalized_provider(&options.provider);
    if provider == "paper" || provider == "simulated" {
        let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())?;
        let account_segments: Vec<_> = segments
            .iter()
            .map(|segment_key| AccountSegment {
                identity: identity.clone(),
                segment_key: segment_key.clone(),
                environment: options.environment.clone(),
                account_model: Some(
                    options
                        .account_model
                        .clone()
                        .or_else(|| {
                            options
                                .product
                                .eq_ignore_ascii_case("margin")
                                .then_some("margin".into())
                        })
                        .unwrap_or_else(|| "no_margin".into()),
                ),
            })
            .collect();
        let source = InMemoryAccountSource {
            snapshots: segments
                .iter()
                .map(|segment| {
                    let mut snapshot = empty_snapshot(segment.clone());
                    snapshot.balances = options
                        .initial_balances
                        .iter()
                        .map(|value| parse_initial_balance(value))
                        .collect::<Result<Vec<_>, _>>()?;
                    Ok((segment.clone(), snapshot))
                })
                .into_iter()
                .collect::<Result<std::collections::BTreeMap<_, _>, String>>()?,
        };
        let application = AccountApplication::with_dependencies(
            account_segments,
            Box::new(source),
            state.map(|path| Box::new(JsonAccountStore::new(path)) as _),
        )
        .map_err(|error| error.to_string())?;
        return Ok(AccountComposition {
            application,
            integration: Integration::new(),
            provider,
            product: ProductFamily::Spot,
        });
    }
    let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    let mut primary_integration = None;
    let mut primary_product = ProductFamily::Spot;
    for segment_key in segments {
        let mut segment_options = options.clone();
        segment_options.product = segment_key.clone();
        let (segment_integration, product) = compose_integration(&segment_options)?;
        let product_name = segment_options.product.trim().to_ascii_lowercase();
        let is_funding = provider == "binance" && product_name == "funding";
        let account_model = options.account_model.clone().unwrap_or_else(|| {
            if is_funding || product == ProductFamily::Spot {
                "no_margin".into()
            } else if matches!(
                product,
                ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
            ) {
                "margin".into()
            } else {
                "contract".into()
            }
        });
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: segment_key.clone(),
            environment: options.environment.clone(),
            account_model: Some(account_model),
        });
        let connection = segment_integration
            .connect_account(&ConnectionSpec {
                connection_id: format!("account.{}.{}.rest", provider, product_name),
                provider: provider.clone(),
                product: if is_funding { None } else { Some(product) },
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountRead,
                credential_id: Some(provider.clone()),
                asset_type: None,
            })
            .map_err(|error| error.to_string())?;
        sources.insert(
            segment_key.clone(),
            Box::new(connection) as Box<dyn AccountReadConnection + Send>,
        );
        if let Ok(connection) =
            segment_integration.connect_account_market_profile(&ConnectionSpec {
                connection_id: format!("account.{}.{}.profile", provider, product_name),
                provider: provider.clone(),
                product: if is_funding { None } else { Some(product) },
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountMarketProfileRead,
                credential_id: Some(provider.clone()),
                asset_type: None,
            })
        {
            profile_sources.insert(
                segment_key.clone(),
                Box::new(connection) as Box<dyn AccountMarketProfileConnection + Send>,
            );
        }
        if primary_integration.is_none() {
            primary_product = product;
            primary_integration = Some(segment_integration);
        }
    }
    let integration = primary_integration.ok_or("at least one account connection is required")?;
    let mut application = AccountApplication::with_dependencies(
        account_segments,
        Box::new(MultiIntegrationAccountSource::new(sources)),
        state.map(|path| Box::new(JsonAccountStore::new(path)) as _),
    )
    .map_err(|error| error.to_string())?;
    if !profile_sources.is_empty() {
        application.attach_market_profile_source(Box::new(
            MultiIntegrationAccountProfileSource::new(profile_sources),
        ));
    }
    Ok(AccountComposition {
        application,
        integration,
        provider,
        product: primary_product,
    })
}

fn parse_initial_balance(value: &str) -> Result<Balance, String> {
    let (asset, quantity) = value
        .split_once('=')
        .ok_or_else(|| format!("initial balance must be ASSET=QUANTITY: {value}"))?;
    let asset_code = asset.trim().to_ascii_uppercase();
    if asset_code.is_empty() {
        return Err("initial balance asset is required".into());
    }
    let quantity = quantity.trim();
    let (negative, quantity) = quantity
        .strip_prefix('-')
        .map(|value| (true, value))
        .unwrap_or((false, quantity.strip_prefix('+').unwrap_or(quantity)));
    let (whole, fraction) = quantity.split_once('.').unwrap_or((quantity, ""));
    if whole.is_empty() && fraction.is_empty()
        || !whole.chars().all(|value| value.is_ascii_digit())
        || !fraction.chars().all(|value| value.is_ascii_digit())
        || fraction.len() > u8::MAX as usize
    {
        return Err(format!("invalid initial balance quantity: {quantity}"));
    }
    let scale = fraction.len() as u8;
    let digits = format!("{whole}{fraction}");
    let mut mantissa = digits
        .parse::<i64>()
        .map_err(|_| format!("initial balance quantity overflows i64: {quantity}"))?;
    if negative {
        mantissa = mantissa
            .checked_neg()
            .ok_or_else(|| format!("initial balance quantity overflows i64: {quantity}"))?;
    }
    Ok(Balance {
        asset_id: format!("asset:{}", asset_code.to_ascii_lowercase()),
        asset_code,
        total: DecimalValue::new(mantissa, scale),
        ..Default::default()
    })
}

pub fn normalized_provider(provider: &str) -> String {
    match provider.trim().to_ascii_lowercase().as_str() {
        "okex" => "okx".into(),
        value => value.into(),
    }
}

pub fn compose_integration(
    options: &AccountOptions,
) -> Result<(Integration, ProductFamily), String> {
    let provider = normalized_provider(&options.provider);
    let product = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    match provider.as_str() {
        "binance" => match product.as_str() {
            "spot" => Ok((
                Integration::new().with_binance_spot_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Spot,
            )),
            "funding" => Ok((
                Integration::new().with_binance_funding_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Spot,
            )),
            "cross-margin" | "margin" => Ok((
                Integration::new()
                    .with_binance_margin_account(
                        ProductFamily::CrossMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CrossMargin,
            )),
            "isolated-margin" => Ok((
                Integration::new()
                    .with_binance_margin_account(
                        ProductFamily::IsolatedMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::IsolatedMargin,
            )),
            "options" => Ok((
                Integration::new().with_binance_options_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Options,
            )),
            "usd-m-futures" | "swap" => Ok((
                Integration::new()
                    .with_binance_futures_account(
                        ProductFamily::UsdMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::UsdMFutures,
            )),
            "coin-m-futures" | "futures" => Ok((
                Integration::new()
                    .with_binance_futures_account(
                        ProductFamily::CoinMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CoinMFutures,
            )),
            _ => Err(format!("unsupported Binance account product: {product}")),
        },
        "okx" => {
            let product = match product.as_str() {
                "spot" => ProductFamily::Spot,
                "cross-margin" => ProductFamily::CrossMargin,
                "isolated-margin" => ProductFamily::IsolatedMargin,
                "swap" | "usd-m-futures" => ProductFamily::UsdMFutures,
                "futures" | "coin-m-futures" => ProductFamily::CoinMFutures,
                "options" => ProductFamily::Options,
                _ => return Err(format!("unsupported OKX account product: {product}")),
            };
            Ok((
                Integration::new()
                    .with_okx_account(
                        product,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.passphrase.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                product,
            ))
        }
        "ibkr" => {
            if !matches!(product.as_str(), "spot" | "equity") {
                return Err(format!("unsupported IBKR account product: {product}"));
            }
            Ok((
                Integration::new().with_ibkr_account(
                    options.host.clone(),
                    options.port,
                    options.client_id,
                ),
                ProductFamily::Spot,
            ))
        }
        _ => Err(format!("unsupported account provider: {provider}")),
    }
}

/// Inspect a live credential through a normalized integration capability.
/// Account administration owns the binding policy; integration only returns
/// provider-neutral discovery facts.
pub fn inspect_account_credential(
    options: &AccountOptions,
) -> Result<ExternalAccountCredentialProfile, String> {
    let (integration, product) = compose_integration(options)?;
    let provider = normalized_provider(&options.provider);
    let product_name = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    let is_funding = provider == "binance" && product_name == "funding";
    let mut connection = integration
        .connect_account_credential_inspection(&ConnectionSpec {
            connection_id: format!("account.{provider}.{product_name}.inspect"),
            provider,
            product: if is_funding { None } else { Some(product) },
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountCredentialInspection,
            credential_id: Some("credential".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    connection.inspect_credential()
}

pub fn compose_binance_transfer(
    options: &AccountOptions,
) -> Result<Box<dyn TransferConnection>, String> {
    Integration::new()
        .with_binance_transfer(
            options.api_key.clone(),
            options.secret.clone(),
            options.base_url.clone(),
        )
        .connect_transfer(&ConnectionSpec {
            connection_id: "account.binance.transfer".into(),
            provider: "binance".into(),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Transfer,
            credential_id: Some("binance".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())
}

pub fn compose_binance_earn(options: &AccountOptions) -> Result<Box<dyn EarnConnection>, String> {
    Integration::new()
        .with_binance_earn(
            options.api_key.clone(),
            options.secret.clone(),
            options.base_url.clone(),
        )
        .connect_earn(&ConnectionSpec {
            connection_id: "account.binance.earn".into(),
            provider: "binance".into(),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Earn,
            credential_id: Some("binance".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())
}
