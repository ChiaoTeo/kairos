//! Workspace-local credential storage shared by Rust processes and kairospy.
//!
//! Workspace owns resource locations. This crate owns the credential document
//! protocol and safe persistence. Provider-specific requirements stay with the
//! capability that consumes a credential.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use kairos_workspace::Workspace;
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum CredentialError {
    #[error("credential I/O failed for {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("invalid credential TOML {path}: {message}")]
    InvalidToml { path: PathBuf, message: String },
    #[error("invalid credential id: {0}")]
    InvalidId(String),
    #[error("invalid credential field: {0}")]
    InvalidField(String),
    #[error("invalid credential provider: {0}")]
    InvalidProvider(String),
    #[error("credential already exists: {0}")]
    AlreadyExists(String),
}

#[derive(Clone, Eq, Ord, PartialEq, PartialOrd)]
pub struct CredentialId(String);

impl CredentialId {
    pub fn new(value: impl Into<String>) -> Result<Self, CredentialError> {
        let value = value.into();
        let valid = !value.is_empty()
            && value.len() <= 128
            && value
                .chars()
                .next()
                .is_some_and(|character| character.is_ascii_alphanumeric())
            && value.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '-' | '_')
            });
        if !valid {
            return Err(CredentialError::InvalidId(value));
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for CredentialId {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_tuple("CredentialId")
            .field(&self.0)
            .finish()
    }
}

impl std::fmt::Display for CredentialId {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Clone)]
pub struct CredentialRecord {
    pub credential_id: String,
    pub provider: String,
    pub role: String,
    values: BTreeMap<String, SecretString>,
}

impl std::fmt::Debug for CredentialRecord {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CredentialRecord")
            .field("credential_id", &self.credential_id)
            .field("provider", &self.provider)
            .field("role", &self.role)
            .field("fields", &self.fields())
            .finish()
    }
}

impl CredentialRecord {
    pub fn new(
        credential_id: impl Into<String>,
        provider: impl Into<String>,
        role: impl Into<String>,
        values: impl IntoIterator<Item = (String, String)>,
    ) -> Result<Self, CredentialError> {
        let credential_id = CredentialId::new(credential_id)?;
        let provider = provider.into().trim().to_ascii_lowercase();
        validate_provider(&provider)?;
        let role = role.into();
        let role = if role.trim().is_empty() {
            "readonly".to_owned()
        } else {
            role.trim().to_owned()
        };
        let mut protected = BTreeMap::new();
        for (name, value) in values {
            validate_field(&name)?;
            if !value.trim().is_empty() {
                protected.insert(name, SecretString::from(value));
            }
        }
        Ok(Self {
            credential_id: credential_id.to_string(),
            provider,
            role,
            values: protected,
        })
    }

    pub fn value(&self, field: &str) -> Option<&SecretString> {
        self.values.get(field)
    }

    pub fn value_owned(&self, field: &str) -> Option<String> {
        self.value(field)
            .map(|value| value.expose_secret().to_owned())
    }

    pub fn api_key_value(&self) -> Option<String> {
        self.value_owned("api_key")
    }

    pub fn secret_value(&self) -> Option<String> {
        self.value_owned("api_secret")
    }

    pub fn passphrase_value(&self) -> Option<String> {
        self.value_owned("passphrase")
    }

    pub fn fields(&self) -> Vec<&str> {
        self.values.keys().map(String::as_str).collect()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CredentialSummary {
    pub credential_id: CredentialId,
    pub provider: String,
    pub role: String,
    pub fields: Vec<String>,
}

impl From<&CredentialRecord> for CredentialSummary {
    fn from(value: &CredentialRecord) -> Self {
        Self {
            credential_id: CredentialId::new(value.credential_id.clone())
                .expect("stored credential ids are validated at construction"),
            provider: value.provider.clone(),
            role: value.role.clone(),
            fields: value.fields().into_iter().map(str::to_owned).collect(),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct CredentialStore {
    pub credentials: Vec<CredentialRecord>,
}

impl CredentialStore {
    pub fn load(path: impl Into<PathBuf>) -> Result<Self, CredentialError> {
        let root = path.into();
        if !root.is_dir() {
            return Ok(Self::default());
        }
        let mut credentials = Vec::new();
        for entry in read_dir(&root)? {
            let path = entry.map_err(|source| io_error(&root, source))?.path();
            if path.extension().and_then(|value| value.to_str()) != Some("toml") {
                continue;
            }
            credentials.push(load_one(&path)?);
        }
        credentials.sort_by(|left, right| left.credential_id.cmp(&right.credential_id));
        let mut ids = BTreeSet::new();
        if let Some(duplicate) = credentials
            .iter()
            .find(|credential| !ids.insert(credential.credential_id.clone()))
        {
            return Err(CredentialError::InvalidToml {
                path: root,
                message: format!("duplicate credential id: {}", duplicate.credential_id),
            });
        }
        Ok(Self { credentials })
    }

    pub fn for_workspace(workspace: &Workspace) -> Result<Self, CredentialError> {
        let root = workspace
            .existing_credentials_root()
            .map_err(|source| io_error(workspace.root(), source))?;
        Self::load(root)
    }

    pub fn find(&self, credential_id: &str) -> Option<&CredentialRecord> {
        self.credentials
            .iter()
            .find(|record| record.credential_id == credential_id)
    }

    pub fn find_provider(
        &self,
        provider: &str,
        requested_id: Option<&str>,
    ) -> Option<&CredentialRecord> {
        self.credentials.iter().find(|record| {
            requested_id.is_none_or(|requested| record.credential_id == requested)
                && record.provider.eq_ignore_ascii_case(provider)
        })
    }

    pub fn summaries(&self) -> Vec<CredentialSummary> {
        self.credentials.iter().map(Into::into).collect()
    }

    pub fn put(
        root: &Path,
        record: &CredentialRecord,
        overwrite: bool,
    ) -> Result<(), CredentialError> {
        ensure_private_directory(root)?;
        let target = root.join(format!("{}.toml", record.credential_id));
        if target.exists() && !overwrite {
            return Err(CredentialError::AlreadyExists(
                record.credential_id.to_string(),
            ));
        }
        write_private_atomic(&target, &credential_toml(record))
    }

    pub fn delete(root: &Path, credential_id: &str) -> Result<bool, CredentialError> {
        let id = CredentialId::new(credential_id.to_owned())?;
        let target = root.join(format!("{id}.toml"));
        match fs::remove_file(&target) {
            Ok(()) => {
                FileSync::sync_directory(root)?;
                Ok(true)
            },
            Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(source) => Err(io_error(&target, source)),
        }
    }
}

fn load_one(path: &Path) -> Result<CredentialRecord, CredentialError> {
    let text = fs::read_to_string(path).map_err(|source| io_error(path, source))?;
    let value: toml::Value =
        toml::from_str(&text).map_err(|_error| CredentialError::InvalidToml {
            path: path.to_path_buf(),
            message: "invalid TOML syntax".to_owned(),
        })?;
    let table = value
        .get("credential")
        .and_then(toml::Value::as_table)
        .ok_or_else(|| CredentialError::InvalidToml {
            path: path.to_path_buf(),
            message: "credential TOML requires [credential]".to_owned(),
        })?;
    let id = table
        .get("id")
        .and_then(toml::Value::as_str)
        .or_else(|| path.file_stem().and_then(|value| value.to_str()))
        .unwrap_or_default();
    if path.file_stem().and_then(|value| value.to_str()) != Some(id) {
        return Err(CredentialError::InvalidToml {
            path: path.to_path_buf(),
            message: "credential id must match its file name".to_owned(),
        });
    }
    let provider = table
        .get("provider")
        .and_then(toml::Value::as_str)
        .unwrap_or_default();
    let role = table
        .get("role")
        .and_then(toml::Value::as_str)
        .unwrap_or("readonly");
    let mut values = Vec::new();
    if let Some(table) = table.get("values").and_then(toml::Value::as_table) {
        for (name, value) in table {
            let value = value.as_str().ok_or_else(|| CredentialError::InvalidToml {
                path: path.to_path_buf(),
                message: format!("credential value {name} must be a string"),
            })?;
            values.push((name.clone(), value.to_owned()));
        }
    }
    CredentialRecord::new(id, provider, role, values).map_err(|error| {
        CredentialError::InvalidToml {
            path: path.to_path_buf(),
            message: error.to_string(),
        }
    })
}

fn credential_toml(record: &CredentialRecord) -> String {
    let mut document = format!(
        "[credential]\nid = {}\nprovider = {}\nrole = {}\n",
        toml_string(&record.credential_id),
        toml_string(&record.provider),
        toml_string(&record.role),
    );
    if !record.values.is_empty() {
        document.push_str("\n[credential.values]\n");
    }
    for (name, value) in &record.values {
        document.push_str(&format!(
            "{name} = {}\n",
            toml_string(value.expose_secret())
        ));
    }
    document
}

fn validate_field(value: &str) -> Result<(), CredentialError> {
    let valid = !value.is_empty()
        && value.len() <= 64
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_lowercase())
        && value.chars().all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
        });
    if valid {
        Ok(())
    } else {
        Err(CredentialError::InvalidField(value.to_owned()))
    }
}

fn validate_provider(value: &str) -> Result<(), CredentialError> {
    let valid = !value.is_empty()
        && value.len() <= 64
        && value
            .chars()
            .next()
            .is_some_and(|character| character.is_ascii_lowercase())
        && value.chars().all(|character| {
            character.is_ascii_lowercase()
                || character.is_ascii_digit()
                || matches!(character, '-' | '_')
        });
    if valid {
        Ok(())
    } else {
        Err(CredentialError::InvalidProvider(value.to_owned()))
    }
}

fn toml_string(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len() + 2);
    encoded.push('"');
    for character in value.chars() {
        match character {
            '"' => encoded.push_str("\\\""),
            '\\' => encoded.push_str("\\\\"),
            '\u{0008}' => encoded.push_str("\\b"),
            '\t' => encoded.push_str("\\t"),
            '\n' => encoded.push_str("\\n"),
            '\u{000C}' => encoded.push_str("\\f"),
            '\r' => encoded.push_str("\\r"),
            value if value.is_control() => {
                encoded.push_str(&format!("\\u{:04X}", value as u32));
            },
            value => encoded.push(value),
        }
    }
    encoded.push('"');
    encoded
}

fn read_dir(path: &Path) -> Result<fs::ReadDir, CredentialError> {
    fs::read_dir(path).map_err(|source| io_error(path, source))
}

fn io_error(path: &Path, source: io::Error) -> CredentialError {
    CredentialError::Io {
        path: path.to_path_buf(),
        source,
    }
}

fn ensure_private_directory(path: &Path) -> Result<(), CredentialError> {
    fs::create_dir_all(path).map_err(|source| io_error(path, source))?;
    set_private_mode(path, 0o700)
}

fn write_private_atomic(path: &Path, contents: &str) -> Result<(), CredentialError> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    ensure_private_directory(parent)?;
    let mut attempt = 0_u32;
    let (temporary, mut file) = loop {
        let candidate = parent.join(format!(
            ".{}.{}.{}.tmp",
            path.file_name()
                .and_then(|value| value.to_str())
                .unwrap_or("credential"),
            std::process::id(),
            attempt
        ));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(file) => break (candidate, file),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                attempt = attempt
                    .checked_add(1)
                    .ok_or_else(|| io_error(&candidate, error))?;
            },
            Err(source) => return Err(io_error(&candidate, source)),
        }
    };
    let result = (|| {
        set_private_mode(&temporary, 0o600)?;
        file.write_all(contents.as_bytes())
            .map_err(|source| io_error(&temporary, source))?;
        file.sync_all()
            .map_err(|source| io_error(&temporary, source))?;
        fs::rename(&temporary, path).map_err(|source| io_error(path, source))?;
        set_private_mode(path, 0o600)?;
        FileSync::sync_directory(parent)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

struct FileSync;

impl FileSync {
    fn sync_directory(path: &Path) -> Result<(), CredentialError> {
        let directory = fs::File::open(path).map_err(|source| io_error(path, source))?;
        directory
            .sync_all()
            .map_err(|source| io_error(path, source))
    }
}

#[cfg(unix)]
fn set_private_mode(path: &Path, mode: u32) -> Result<(), CredentialError> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(mode))
        .map_err(|source| io_error(path, source))
}

#[cfg(not(unix))]
fn set_private_mode(_path: &Path, _mode: u32) -> Result<(), CredentialError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{CredentialRecord, CredentialStore};

    #[test]
    fn reads_the_cross_language_credential_fixture() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../tests/fixtures/credentials");
        let store = CredentialStore::load(&root).unwrap();
        let credential = store.find("shared-okx").unwrap();
        assert_eq!(credential.provider, "okx");
        assert_eq!(credential.role, "trade");
        assert_eq!(
            credential.value_owned("account_label").as_deref(),
            Some("fixture-account")
        );
        assert_eq!(
            credential.passphrase_value().as_deref(),
            Some("fixture-passphrase")
        );

        let directory = tempfile::tempdir().unwrap();
        CredentialStore::put(directory.path(), credential, false).unwrap();
        assert_eq!(
            std::fs::read_to_string(directory.path().join("shared-okx.toml")).unwrap(),
            std::fs::read_to_string(root.join("shared-okx.toml")).unwrap()
        );
    }

    #[test]
    fn round_trips_generic_values_without_debug_exposure() {
        let directory = tempfile::tempdir().unwrap();
        let record = CredentialRecord::new(
            "telegram-main",
            "TELEGRAM",
            "notify",
            [("bot_token".to_owned(), "do-not-log".to_owned())],
        )
        .unwrap();
        assert!(!format!("{record:?}").contains("do-not-log"));

        CredentialStore::put(directory.path(), &record, false).unwrap();
        let loaded = CredentialStore::load(directory.path()).unwrap();
        assert_eq!(loaded.credentials[0].provider, "telegram");
        assert_eq!(
            loaded.credentials[0].value_owned("bot_token").as_deref(),
            Some("do-not-log")
        );
    }

    #[test]
    fn put_does_not_delete_unrelated_credentials() {
        let directory = tempfile::tempdir().unwrap();
        let first = CredentialRecord::new(
            "first",
            "binance",
            "readonly",
            [("api_key".to_owned(), "one".to_owned())],
        )
        .unwrap();
        let second = CredentialRecord::new(
            "second",
            "massive",
            "readonly",
            [("api_key".to_owned(), "two".to_owned())],
        )
        .unwrap();
        CredentialStore::put(directory.path(), &first, false).unwrap();
        CredentialStore::put(directory.path(), &second, false).unwrap();
        assert_eq!(
            CredentialStore::load(directory.path())
                .unwrap()
                .credentials
                .len(),
            2
        );
    }

    #[test]
    fn parse_errors_do_not_echo_secret_contents() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("broken.toml"),
            "[credential.values]\napi_secret = \"do-not-echo\n",
        )
        .unwrap();
        let error = CredentialStore::load(directory.path()).unwrap_err();
        assert!(!error.to_string().contains("do-not-echo"));
    }

    #[test]
    fn credential_id_must_match_its_file_name() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("wrong.toml"),
            "[credential]\nid = \"right\"\nprovider = \"paper\"\n",
        )
        .unwrap();
        let error = CredentialStore::load(directory.path()).unwrap_err();
        assert!(error.to_string().contains("must match its file name"));
    }

    #[test]
    fn control_characters_are_escaped_in_toml_values() {
        let directory = tempfile::tempdir().unwrap();
        let record = CredentialRecord::new(
            "multiline",
            "custom",
            "readonly",
            [("token".to_owned(), "first\nsecond".to_owned())],
        )
        .unwrap();
        CredentialStore::put(directory.path(), &record, false).unwrap();
        let document = std::fs::read_to_string(directory.path().join("multiline.toml")).unwrap();
        assert!(document.contains("token = \"first\\nsecond\""));
        assert_eq!(
            CredentialStore::load(directory.path()).unwrap().credentials[0]
                .value_owned("token")
                .as_deref(),
            Some("first\nsecond")
        );
    }
}
