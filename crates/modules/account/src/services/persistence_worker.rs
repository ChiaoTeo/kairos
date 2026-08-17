use crate::domain::{Account, AccountEvent};
use crate::services::persistence::JsonAccountStore;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

enum PersistenceJob {
    Append {
        events: Vec<AccountEvent>,
        response: Option<SyncSender<Result<(), String>>>,
    },
    Checkpoint {
        actor_id: String,
        generation: u64,
        event_sequence: u64,
        accounts: Vec<Account>,
        response: SyncSender<Result<(), String>>,
    },
}

/// Owns blocking account journal/checkpoint I/O on a dedicated thread.
/// Callers wait only for the durability acknowledgement; filesystem work is
/// never performed by the account state owner itself.
pub(crate) struct AccountPersistenceWorker {
    sender: Option<SyncSender<PersistenceJob>>,
    handle: Option<JoinHandle<()>>,
    last_error: Arc<Mutex<Option<String>>>,
    pending: Arc<AtomicUsize>,
}

impl AccountPersistenceWorker {
    pub(crate) fn new(mut store: JsonAccountStore) -> Self {
        let (sender, receiver) = mpsc::sync_channel(8);
        let last_error = Arc::new(Mutex::new(None));
        let worker_error = Arc::clone(&last_error);
        let pending = Arc::new(AtomicUsize::new(0));
        let worker_pending = Arc::clone(&pending);
        let handle = thread::Builder::new()
            .name("kairos-account-persistence".into())
            .spawn(move || {
                while let Ok(job) = receiver.recv() {
                    match job {
                        PersistenceJob::Append { events, response } => {
                            let mut batch = vec![(events, response)];
                            let mut checkpoint = None;
                            while let Ok(next) = receiver.try_recv() {
                                match next {
                                    PersistenceJob::Append { events, response } => {
                                        batch.push((events, response));
                                    }
                                    other @ PersistenceJob::Checkpoint { .. } => {
                                        checkpoint = Some(other);
                                        break;
                                    }
                                }
                            }
                            let mut all_events = Vec::new();
                            for (events, _) in &batch {
                                all_events.extend(events.iter().cloned());
                            }
                            let result = store.append_events(&all_events);
                            if let Err(error) = &result {
                                record_error(&worker_error, error);
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
                                response,
                            }) = checkpoint
                            {
                                let result =
                                    store.save(&actor_id, generation, event_sequence, &accounts);
                                if let Err(error) = &result {
                                    record_error(&worker_error, error);
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
                            response,
                        } => {
                            let result =
                                store.save(&actor_id, generation, event_sequence, &accounts);
                            if let Err(error) = &result {
                                record_error(&worker_error, error);
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
            last_error,
            pending,
        }
    }

    pub(crate) fn append_events(&self, events: Vec<AccountEvent>) -> Result<(), String> {
        if events.is_empty() {
            return Ok(());
        }
        let (response, receiver) = mpsc::sync_channel(1);
        self.pending.fetch_add(1, Ordering::Relaxed);
        if let Err(error) = self
            .sender
            .as_ref()
            .ok_or_else(|| "account persistence worker is stopped".to_string())?
            .send(PersistenceJob::Append {
                events,
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
    pub(crate) fn enqueue_events(&self, events: Vec<AccountEvent>) -> Result<(), String> {
        if events.is_empty() {
            return Ok(());
        }
        let sender = self
            .sender
            .as_ref()
            .ok_or_else(|| "account persistence worker is stopped".to_string())?;
        self.pending.fetch_add(1, Ordering::Relaxed);
        match sender.try_send(PersistenceJob::Append {
            events,
            response: None,
        }) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => {
                self.pending.fetch_sub(1, Ordering::Relaxed);
                Err("account persistence queue is full".to_string())
            }
            Err(TrySendError::Disconnected(_)) => {
                self.pending.fetch_sub(1, Ordering::Relaxed);
                Err("account persistence worker is stopped".to_string())
            }
        }
    }

    pub(crate) fn take_error(&self) -> Option<String> {
        self.last_error
            .lock()
            .ok()
            .and_then(|mut error| error.take())
    }

    pub(crate) fn checkpoint(
        &self,
        actor_id: String,
        generation: u64,
        event_sequence: u64,
        accounts: Vec<Account>,
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

fn record_error(last_error: &Arc<Mutex<Option<String>>>, error: &str) {
    if let Ok(mut last_error) = last_error.lock() {
        *last_error = Some(error.to_owned());
    }
}
