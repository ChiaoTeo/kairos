use crate::application::RiskSnapshot;
use crate::domain::RiskPolicy;
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

/// Durable facts are append-only.  The state owner acknowledges a mutating
/// command only after this record has reached the journal; snapshots are
/// checkpoints and never the source of truth.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) enum PersistedEvent {
    PolicyActivated {
        sequence: u64,
        policy: RiskPolicy,
    },
    ReservationChanged {
        sequence: u64,
        reservation: crate::domain::Reservation,
    },
    CircuitChanged {
        sequence: u64,
        circuit: crate::domain::CircuitState,
    },
}

impl PersistedEvent {
    pub(crate) fn sequence(&self) -> u64 {
        match self {
            Self::PolicyActivated { sequence, .. }
            | Self::ReservationChanged { sequence, .. }
            | Self::CircuitChanged { sequence, .. } => *sequence,
        }
    }
}

pub(crate) struct RecoveredState {
    pub snapshot: Option<RiskSnapshot>,
    pub events: Vec<PersistedEvent>,
}

pub(crate) trait RiskStateStore: Send {
    fn load(&mut self) -> Result<RecoveredState, String>;
    fn append(&mut self, event: &PersistedEvent) -> Result<(), String>;
    fn checkpoint(&mut self, snapshot: &RiskSnapshot) -> Result<(), String>;
}

/// A small, deterministic local journal.  It uses JSON lines deliberately so
/// recovery and manual inspection remain straightforward; the state owner
/// still avoids serializing the whole state for every command.
pub(crate) struct JournalRiskStore {
    checkpoint_path: PathBuf,
    journal_path: PathBuf,
    journal: Option<File>,
    checkpoint_every: u64,
}

impl JournalRiskStore {
    pub fn new(checkpoint_path: impl Into<PathBuf>) -> Self {
        let checkpoint_path = checkpoint_path.into();
        let journal_path = checkpoint_path.with_extension("journal");
        Self {
            checkpoint_path,
            journal_path,
            journal: None,
            checkpoint_every: 128,
        }
    }

    fn open_journal(&mut self) -> Result<&mut File, String> {
        if self.journal.is_none() {
            if let Some(parent) = self.journal_path.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            self.journal = Some(
                OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&self.journal_path)
                    .map_err(|error| error.to_string())?,
            );
        }
        self.journal
            .as_mut()
            .ok_or_else(|| "risk journal is unavailable".to_string())
    }
}

impl RiskStateStore for JournalRiskStore {
    fn load(&mut self) -> Result<RecoveredState, String> {
        let snapshot = if self.checkpoint_path.exists() {
            let bytes = fs::read(&self.checkpoint_path).map_err(|error| error.to_string())?;
            Some(serde_json::from_slice(&bytes).map_err(|error| error.to_string())?)
        } else {
            None
        };
        let checkpoint_sequence = snapshot
            .as_ref()
            .map_or(0, |s: &RiskSnapshot| s.event_sequence.get());
        let mut events = Vec::new();
        if self.journal_path.exists() {
            let file = File::open(&self.journal_path).map_err(|error| error.to_string())?;
            for line in BufReader::new(file).lines() {
                let line = line.map_err(|error| error.to_string())?;
                if line.trim().is_empty() {
                    continue;
                }
                let event: PersistedEvent =
                    serde_json::from_str(&line).map_err(|error| error.to_string())?;
                if event.sequence() > checkpoint_sequence {
                    events.push(event);
                }
            }
        }
        let _ = self.open_journal()?;
        Ok(RecoveredState { snapshot, events })
    }

    fn append(&mut self, event: &PersistedEvent) -> Result<(), String> {
        let file = self.open_journal()?;
        serde_json::to_writer(&mut *file, event).map_err(|error| error.to_string())?;
        file.write_all(b"\n").map_err(|error| error.to_string())?;
        file.sync_data().map_err(|error| error.to_string())
    }

    fn checkpoint(&mut self, snapshot: &RiskSnapshot) -> Result<(), String> {
        if snapshot.event_sequence == kairos_domain_types::Sequence::new(0)
            || !snapshot
                .event_sequence
                .is_multiple_of(self.checkpoint_every)
        {
            return Ok(());
        }
        if let Some(parent) = self.checkpoint_path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.checkpoint_path.with_extension("checkpoint.tmp");
        let bytes = serde_json::to_vec(snapshot).map_err(|error| error.to_string())?;
        let mut file = File::create(&temporary).map_err(|error| error.to_string())?;
        file.write_all(&bytes).map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        fs::rename(temporary, &self.checkpoint_path).map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn journal_round_trips_events_without_snapshot_rewrite() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("risk-state.json");
        let mut store = JournalRiskStore::new(path);
        let event = PersistedEvent::PolicyActivated {
            sequence: 1,
            policy: RiskPolicy {
                policy_id: kairos_domain_types::PolicyId::new("p").unwrap(),
                version: 1.into(),
                scope: crate::domain::PolicyScope {
                    account_id: Some(kairos_domain_types::AccountId::new("a").unwrap()),
                    strategy_id: None,
                    instrument_id: None,
                    exchange_id: None,
                },
                metric: crate::domain::Metric::Notional,
                limit: crate::domain::Amount::new(100, 0).unwrap(),
                enforcement: crate::domain::EnforcementMode::Reject,
                valid_from_unix_nanos: 0.into(),
                valid_until_unix_nanos: None,
                window_nanos: None,
            },
        };
        store.append(&event).unwrap();
        let recovered = store.load().unwrap();
        assert_eq!(recovered.events.len(), 1);
        assert_eq!(recovered.events[0].sequence(), 1);
    }
}
