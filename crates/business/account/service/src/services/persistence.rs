use crate::domain::{Account, AccountEvent, AccountSegment, AccountState};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

const ACCOUNT_STATE_SCHEMA_VERSION: u32 = 1;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub(crate) struct PersistedAccounts {
    pub schema_version: u32,
    pub actor_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub accounts: Vec<(AccountSegment, AccountState)>,
}

pub struct JsonAccountStore {
    pub path: PathBuf,
}

impl JsonAccountStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    pub(crate) fn load(&mut self) -> Result<PersistedAccounts, String> {
        if !self.path.exists() {
            return Ok(PersistedAccounts {
                schema_version: ACCOUNT_STATE_SCHEMA_VERSION,
                actor_id: "account".into(),
                generation: 0,
                event_sequence: 0,
                accounts: Vec::new(),
            });
        }
        let data = std::fs::read(&self.path).map_err(|error| error.to_string())?;
        let value: serde_json::Value =
            serde_json::from_slice(&data).map_err(|error| error.to_string())?;
        if value.is_array() {
            let accounts = serde_json::from_value(value).map_err(|error| error.to_string())?;
            return Ok(PersistedAccounts {
                schema_version: ACCOUNT_STATE_SCHEMA_VERSION,
                actor_id: "account".into(),
                generation: 0,
                event_sequence: 0,
                accounts,
            });
        }
        let persisted: PersistedAccounts =
            serde_json::from_value(value).map_err(|error| error.to_string())?;
        if persisted.schema_version != ACCOUNT_STATE_SCHEMA_VERSION {
            return Err(format!(
                "unsupported account state schema version: {}",
                persisted.schema_version
            ));
        }
        Ok(persisted)
    }

    pub(crate) fn load_events(&self) -> Result<Vec<AccountEvent>, String> {
        let path = self.journal_path();
        if !path.exists() {
            return Ok(Vec::new());
        }
        let data = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
        data.lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| serde_json::from_str(line).map_err(|error| error.to_string()))
            .collect()
    }

    pub(crate) fn append_events(&mut self, events: &[AccountEvent]) -> Result<(), String> {
        if events.is_empty() {
            return Ok(());
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.journal_path())
            .map_err(|error| error.to_string())?;
        let payload = events.iter().try_fold(Vec::new(), |mut payload, event| {
            payload.extend(serde_json::to_vec(event).map_err(|error| error.to_string())?);
            payload.push(b'\n');
            Ok::<_, String>(payload)
        })?;
        file.write_all(&payload)
            .map_err(|error| error.to_string())?;
        file.sync_data().map_err(|error| error.to_string())
    }

    pub(crate) fn save(
        &mut self,
        actor_id: &str,
        generation: u64,
        event_sequence: u64,
        accounts: &[Account],
    ) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let values: Vec<_> = accounts
            .iter()
            .map(|account| (account.segment().clone(), account.state().clone()))
            .collect();
        let payload = serde_json::to_vec_pretty(&PersistedAccounts {
            schema_version: ACCOUNT_STATE_SCHEMA_VERSION,
            actor_id: actor_id.into(),
            generation,
            event_sequence,
            accounts: values,
        })
        .map_err(|error| error.to_string())?;
        let temporary = self.path.with_extension("tmp");
        std::fs::write(&temporary, payload).map_err(|error| error.to_string())?;
        std::fs::rename(temporary, &self.path).map_err(|error| error.to_string())?;
        match std::fs::remove_file(self.journal_path()) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.to_string()),
        }
    }

    fn journal_path(&self) -> PathBuf {
        self.path.with_extension("events.jsonl")
    }
}
