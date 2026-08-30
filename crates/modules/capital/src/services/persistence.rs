use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::domain::{CapitalSnapshot, FundingObjectiveRecord};

const CAPITAL_STATE_SCHEMA_VERSION: u32 = 3;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "record", rename_all = "snake_case")]
pub(crate) enum CapitalJournalRecord {
    ObjectiveChanged {
        journal_sequence: u64,
        event_sequence: u64,
        objective: Box<FundingObjectiveRecord>,
    },
    DemandChanged {
        journal_sequence: u64,
        event_sequence: u64,
        demand: Box<crate::domain::CapitalDemandRecord>,
    },
    PolicyChanged {
        journal_sequence: u64,
        event_sequence: u64,
        policy: Box<crate::domain::CapitalPolicy>,
        #[serde(default)]
        occurred_at: kairos_primitives::time::UnixNanos,
    },
    FactsObserved {
        journal_sequence: u64,
        event_sequence: u64,
        facts: Box<crate::domain::CapitalFacts>,
    },
    AvailabilityBatchEvaluated {
        journal_sequence: u64,
        event_sequence: u64,
        availability: Vec<crate::domain::CapitalAvailabilityView>,
    },
    RouteChanged {
        journal_sequence: u64,
        event_sequence: u64,
        route: Box<crate::domain::CapitalTransferRoute>,
        #[serde(default)]
        occurred_at: kairos_primitives::time::UnixNanos,
    },
    PlanAuthorized {
        journal_sequence: u64,
        event_sequence: u64,
        plan: Box<crate::domain::CapitalPlan>,
        reservation: Box<crate::domain::CapitalReservation>,
    },
    PlanStateChanged {
        journal_sequence: u64,
        event_sequence: u64,
        plan: Box<crate::domain::CapitalPlan>,
        reservation: Box<crate::domain::CapitalReservation>,
        operation: Box<crate::domain::CapitalOperation>,
    },
    PlanExpired {
        journal_sequence: u64,
        event_sequence: u64,
        plan: Box<crate::domain::CapitalPlan>,
        reservation: Box<crate::domain::CapitalReservation>,
        operation: Option<Box<crate::domain::CapitalOperation>>,
    },
    PublicationAcknowledged {
        journal_sequence: u64,
        event_sequence: u64,
    },
}

impl CapitalJournalRecord {
    pub(crate) fn journal_sequence(&self) -> u64 {
        match self {
            Self::ObjectiveChanged {
                journal_sequence, ..
            }
            | Self::DemandChanged {
                journal_sequence, ..
            }
            | Self::PolicyChanged {
                journal_sequence, ..
            }
            | Self::FactsObserved {
                journal_sequence, ..
            }
            | Self::AvailabilityBatchEvaluated {
                journal_sequence, ..
            }
            | Self::RouteChanged {
                journal_sequence, ..
            }
            | Self::PlanAuthorized {
                journal_sequence, ..
            }
            | Self::PlanStateChanged {
                journal_sequence, ..
            }
            | Self::PlanExpired {
                journal_sequence, ..
            }
            | Self::PublicationAcknowledged {
                journal_sequence, ..
            } => *journal_sequence,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct PersistedCapitalSnapshot {
    schema_version: u32,
    snapshot: CapitalSnapshot,
}

pub(crate) struct RecoveredCapitalState {
    pub snapshot: Option<CapitalSnapshot>,
    pub records: Vec<CapitalJournalRecord>,
}

pub(crate) struct JournalCapitalStore {
    checkpoint_path: PathBuf,
    journal_path: PathBuf,
    journal: Option<File>,
}

impl JournalCapitalStore {
    pub(crate) fn new(checkpoint_path: impl Into<PathBuf>) -> Self {
        let checkpoint_path = checkpoint_path.into();
        let journal_path = checkpoint_path.with_extension("journal.jsonl");
        Self {
            checkpoint_path,
            journal_path,
            journal: None,
        }
    }

    pub(crate) fn load(&mut self) -> Result<RecoveredCapitalState, String> {
        let snapshot = if self.checkpoint_path.exists() {
            let persisted: PersistedCapitalSnapshot = serde_json::from_slice(
                &fs::read(&self.checkpoint_path).map_err(|error| error.to_string())?,
            )
            .map_err(|error| error.to_string())?;
            if persisted.schema_version != CAPITAL_STATE_SCHEMA_VERSION {
                return Err(format!(
                    "unsupported Capital state schema version: {}",
                    persisted.schema_version
                ));
            }
            Some(persisted.snapshot)
        } else {
            None
        };
        let checkpoint_sequence = snapshot
            .as_ref()
            .map_or(0, |snapshot| snapshot.journal_sequence.get());
        let mut records = Vec::new();
        if self.journal_path.exists() {
            for line in
                BufReader::new(File::open(&self.journal_path).map_err(|error| error.to_string())?)
                    .lines()
            {
                let line = line.map_err(|error| error.to_string())?;
                if line.trim().is_empty() {
                    continue;
                }
                let record: CapitalJournalRecord =
                    serde_json::from_str(&line).map_err(|error| error.to_string())?;
                if record.journal_sequence() > checkpoint_sequence {
                    records.push(record);
                }
            }
        }
        let _ = self.open_journal()?;
        Ok(RecoveredCapitalState { snapshot, records })
    }

    pub(crate) fn append(&mut self, record: &CapitalJournalRecord) -> Result<(), String> {
        let file = self.open_journal()?;
        serde_json::to_writer(&mut *file, record).map_err(|error| error.to_string())?;
        file.write_all(b"\n").map_err(|error| error.to_string())?;
        file.sync_data().map_err(|error| error.to_string())
    }

    pub(crate) fn checkpoint(&self, snapshot: &CapitalSnapshot) -> Result<(), String> {
        if let Some(parent) = self.checkpoint_path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.checkpoint_path.with_extension("checkpoint.tmp");
        let payload = serde_json::to_vec(&PersistedCapitalSnapshot {
            schema_version: CAPITAL_STATE_SCHEMA_VERSION,
            snapshot: snapshot.clone(),
        })
        .map_err(|error| error.to_string())?;
        let mut file = File::create(&temporary).map_err(|error| error.to_string())?;
        file.write_all(&payload)
            .map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        fs::rename(temporary, &self.checkpoint_path).map_err(|error| error.to_string())
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
            .ok_or_else(|| "Capital journal is unavailable".into())
    }
}
