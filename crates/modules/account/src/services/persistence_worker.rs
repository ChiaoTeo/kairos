use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, SyncSender, TrySendError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::domain::Account;
use crate::services::persistence::{AccountJournalRecord, JsonAccountStore};

enum PersistenceJob {
    Append {
        records: Vec<AccountJournalRecord>,
        response: Option<SyncSender<Result<(), String>>>,
    },
    Checkpoint {
        actor_id: String,
        generation: u64,
        event_sequence: u64,
        accounts: Vec<Account>,
        pending_business_events: Vec<crate::application::AccountBusinessEvent>,
        response: SyncSender<Result<(), String>>,
    },
}

/// Owns blocking account journal/checkpoint I/O on a dedicated thread.
/// Callers wait only for the durability acknowledgement; filesystem work is
/// never performed by the account state owner itself.
pub(crate) struct AccountPersistenceWorker {
    sender: Option<SyncSender<PersistenceJob>>,
    handle: Option<JoinHandle<()>>,
    pending: Arc<AtomicUsize>,
}

impl AccountPersistenceWorker {
    pub(crate) fn new(mut store: JsonAccountStore) -> Self {
        let (sender, receiver) = mpsc::sync_channel(8);
        let pending = Arc::new(AtomicUsize::new(0));
        let worker_pending = Arc::clone(&pending);
        let handle = thread::Builder::new()
            .name("kairos-account-persistence".into())
            .spawn(move || {
                while let Ok(job) = receiver.recv() {
                    match job {
                        PersistenceJob::Append { records, response } => {
                            let mut batch = vec![(records, response)];
                            let mut checkpoint = None;
                            while let Ok(next) = receiver.try_recv() {
                                match next {
                                    PersistenceJob::Append { records, response } => {
                                        batch.push((records, response));
                                    }
                                    other @ PersistenceJob::Checkpoint { .. } => {
                                        checkpoint = Some(other);
                                        break;
                                    }
                                }
                            }
                            let mut all_records = Vec::new();
                            for (records, _) in &batch {
                                all_records.extend(records.iter().cloned());
                            }
                            let result = store.append_journal(&all_records);
                            if let Err(error) = &result {
                                tracing::error!(event = "account_persistence_failed", error = %error, "Account journal persistence failed");
                            }
                            for (_, response) in batch {
                                if let Some(response) = response {
                                    let _ = response.send(result.clone());
                                }
                                worker_pending.fetch_sub(1, Ordering::Relaxed);
                            }
                            if let Some(PersistenceJob::Checkpoint {
                                actor_id,
                                generation,
                                event_sequence,
                                accounts,
                                pending_business_events,
                                response,
                            }) = checkpoint
                            {
                                let result = store.save(
                                    &actor_id,
                                    generation,
                                    event_sequence,
                                    &accounts,
                                    &pending_business_events,
                                );
                                if let Err(error) = &result {
                                    tracing::error!(event = "account_persistence_failed", error = %error, "Account checkpoint persistence failed");
                                }
                                let _ = response.send(result);
                                worker_pending.fetch_sub(1, Ordering::Relaxed);
                            }
                            continue;
                        }
                        PersistenceJob::Checkpoint {
                            actor_id,
                            generation,
                            event_sequence,
                            accounts,
                            pending_business_events,
                            response,
                        } => {
                            let result = store.save(
                                &actor_id,
                                generation,
                                event_sequence,
                                &accounts,
                                &pending_business_events,
                            );
                            if let Err(error) = &result {
                                tracing::error!(event = "account_persistence_failed", error = %error, "Account checkpoint persistence failed");
                            }
                            let _ = response.send(result);
                        }
                    }
                    worker_pending.fetch_sub(1, Ordering::Relaxed);
                }
            })
            .expect("account persistence worker thread should start");
        Self {
            sender: Some(sender),
            handle: Some(handle),
            pending,
        }
    }

    pub(crate) fn append_journal(&self, records: Vec<AccountJournalRecord>) -> Result<(), String> {
        if records.is_empty() {
            return Ok(());
        }
        let (response, receiver) = mpsc::sync_channel(1);
        self.pending.fetch_add(1, Ordering::Relaxed);
        if let Err(error) = self
            .sender
            .as_ref()
            .ok_or_else(|| "account persistence worker is stopped".to_string())?
            .send(PersistenceJob::Append {
                records,
                response: Some(response),
            })
            .map_err(|_| "account persistence worker is stopped".to_string())
        {
            self.pending.fetch_sub(1, Ordering::Relaxed);
            return Err(error);
        }
        receiver
            .recv()
            .map_err(|_| "account persistence worker stopped before acknowledgement".to_string())?
    }

    /// Enqueue ordinary stream events without making the account loop wait
    /// for fsync. The bounded queue still applies backpressure if storage is
    /// persistently slower than the event source.
    pub(crate) fn enqueue_journal(&self, records: Vec<AccountJournalRecord>) -> Result<(), String> {
        if records.is_empty() {
            return Ok(());
        }
        let sender = self
            .sender
            .as_ref()
            .ok_or_else(|| "account persistence worker is stopped".to_string())?;
        self.pending.fetch_add(1, Ordering::Relaxed);
        match sender.try_send(PersistenceJob::Append {
            records,
            response: None,
        }) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => {
                self.pending.fetch_sub(1, Ordering::Relaxed);
                Err("account persistence queue is full".to_string())
            },
            Err(TrySendError::Disconnected(_)) => {
                self.pending.fetch_sub(1, Ordering::Relaxed);
                Err("account persistence worker is stopped".to_string())
            },
        }
    }

    pub(crate) fn checkpoint(
        &self,
        actor_id: String,
        generation: u64,
        event_sequence: u64,
        accounts: Vec<Account>,
        pending_business_events: Vec<crate::application::AccountBusinessEvent>,
    ) -> Result<(), String> {
        let (response, receiver) = mpsc::sync_channel(1);
        self.pending.fetch_add(1, Ordering::Relaxed);
        if let Err(error) = self
            .sender
            .as_ref()
            .ok_or_else(|| "account persistence worker is stopped".to_string())?
            .send(PersistenceJob::Checkpoint {
                actor_id,
                generation,
                event_sequence,
                accounts,
                pending_business_events,
                response,
            })
            .map_err(|_| "account persistence worker is stopped".to_string())
        {
            self.pending.fetch_sub(1, Ordering::Relaxed);
            return Err(error);
        }
        receiver
            .recv()
            .map_err(|_| "account persistence worker stopped before acknowledgement".to_string())?
    }
}

impl Drop for AccountPersistenceWorker {
    fn drop(&mut self) {
        self.sender.take();
        if let Some(handle) = self.handle.take() {
            let started = Instant::now();
            while !handle.is_finished() && started.elapsed() < Duration::from_millis(100) {
                thread::sleep(Duration::from_millis(1));
            }
            if handle.is_finished() {
                let _ = handle.join();
            }
            // Dropping a non-finished handle detaches it. This keeps account
            // shutdown bounded if the filesystem is stuck.
        }
    }
}
