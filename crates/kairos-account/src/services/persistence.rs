use crate::application::protocol::AccountStateStore;
use crate::domain::{Account, AccountSegment, AccountState};
use std::path::PathBuf;

#[derive(Default)]
pub struct MemoryAccountStore {
    pub accounts: Vec<(AccountSegment, AccountState)>,
}

impl AccountStateStore for MemoryAccountStore {
    fn load(&mut self) -> Result<Vec<(AccountSegment, AccountState)>, String> {
        Ok(self.accounts.clone())
    }

    fn save(&mut self, accounts: &[Account]) -> Result<(), String> {
        self.accounts = accounts
            .iter()
            .map(|account| (account.segment.clone(), account.state.clone()))
            .collect();
        Ok(())
    }
}

pub struct JsonAccountStore {
    pub path: PathBuf,
}

impl JsonAccountStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl AccountStateStore for JsonAccountStore {
    fn load(&mut self) -> Result<Vec<(AccountSegment, AccountState)>, String> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let data = std::fs::read(&self.path).map_err(|error| error.to_string())?;
        serde_json::from_slice(&data).map_err(|error| error.to_string())
    }

    fn save(&mut self, accounts: &[Account]) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let values: Vec<_> = accounts
            .iter()
            .map(|account| (account.segment.clone(), account.state.clone()))
            .collect();
        let payload = serde_json::to_vec_pretty(&values).map_err(|error| error.to_string())?;
        let temporary = self.path.with_extension("tmp");
        std::fs::write(&temporary, payload).map_err(|error| error.to_string())?;
        std::fs::rename(temporary, &self.path).map_err(|error| error.to_string())
    }
}
