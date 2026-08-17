use crate::domain::{AccountSegment, AccountSnapshot};
use crate::services::integration::AccountSnapshotGateway;
use std::collections::BTreeMap;
use std::sync::mpsc::{self, Receiver, SyncSender, TryRecvError, TrySendError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const SEGMENT_REFRESH_TIMEOUT: Duration = Duration::from_secs(30);
const CIRCUIT_FAILURE_THRESHOLD: u32 = 3;
const CIRCUIT_COOLDOWN: Duration = Duration::from_secs(30);

#[derive(Clone)]
pub(crate) struct RefreshFetch {
    pub segment: AccountSegment,
    pub result: Result<AccountSnapshot, String>,
    pub elapsed_ms: u64,
}

struct RefreshJob {
    segment: AccountSegment,
    response: SyncSender<RefreshFetch>,
}

struct SegmentWorker {
    sender: Option<SyncSender<RefreshJob>>,
    handle: Option<JoinHandle<()>>,
}

/// Owns provider snapshot I/O on bounded, per-segment workers. A slow
/// connection therefore cannot serialize refreshes for unrelated segments.
pub(crate) struct AccountRefreshWorker {
    workers: BTreeMap<String, SegmentWorker>,
}

impl AccountRefreshWorker {
    pub(crate) fn new(source: AccountSnapshotGateway) -> Self {
        let workers = source
            .split()
            .into_iter()
            .map(|(key, mut source)| {
                let (sender, receiver) = mpsc::sync_channel::<RefreshJob>(2);
                let name = format!("kairos-account-refresh-{key}");
                let handle = thread::Builder::new()
                    .name(name)
                    .spawn(move || {
                        let mut consecutive_failures = 0_u32;
                        let mut circuit_open_until = None;
                        while let Ok(job) = receiver.recv() {
                            if let Some(until) = circuit_open_until {
                                if Instant::now() < until {
                                    let elapsed_ms = 0;
                                    let fetch = RefreshFetch {
                                        segment: job.segment,
                                        result: Err("account refresh circuit is open".into()),
                                        elapsed_ms,
                                    };
                                    if job.response.send(fetch).is_err() {
                                        break;
                                    }
                                    continue;
                                }
                                circuit_open_until = None;
                            }
                            let started = Instant::now();
                            let mut result = source.fetch(&job.segment);
                            let elapsed = started.elapsed();
                            if elapsed > SEGMENT_REFRESH_TIMEOUT {
                                result = Err(format!(
                                    "account segment refresh timed out after {}ms",
                                    elapsed.as_millis()
                                ));
                            }
                            if result.is_err() {
                                consecutive_failures = consecutive_failures.saturating_add(1);
                                if consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD {
                                    circuit_open_until = Some(Instant::now() + CIRCUIT_COOLDOWN);
                                }
                            } else {
                                consecutive_failures = 0;
                            }
                            let fetch = RefreshFetch {
                                segment: job.segment,
                                result,
                                elapsed_ms: elapsed.as_millis() as u64,
                            };
                            if job.response.send(fetch).is_err() {
                                break;
                            }
                        }
                    })
                    .expect("account refresh worker thread should start");
                (
                    key,
                    SegmentWorker {
                        sender: Some(sender),
                        handle: Some(handle),
                    },
                )
            })
            .collect();
        Self { workers }
    }

    pub(crate) fn submit(
        &self,
        segments: Vec<AccountSegment>,
    ) -> Result<Receiver<Vec<RefreshFetch>>, String> {
        let (response, receiver) = mpsc::sync_channel(1);
        let (fetch_sender, fetch_receiver) = mpsc::sync_channel(segments.len().max(1));
        for segment in segments.iter().cloned() {
            let worker = self
                .workers
                .get(segment.segment_key.as_str())
                .ok_or_else(|| {
                    format!(
                        "account refresh worker missing segment: {}",
                        segment.segment_key
                    )
                })?;
            let sender = worker
                .sender
                .as_ref()
                .ok_or_else(|| "account refresh worker is stopped".to_string())?;
            sender
                .try_send(RefreshJob {
                    segment,
                    response: fetch_sender.clone(),
                })
                .map_err(|error| match error {
                    TrySendError::Full(_) => "account refresh queue is full".to_string(),
                    TrySendError::Disconnected(_) => {
                        "account refresh worker is stopped".to_string()
                    }
                })?;
        }
        drop(fetch_sender);
        thread::spawn(move || {
            let mut fetches = Vec::with_capacity(segments.len());
            for _ in segments {
                match fetch_receiver.recv() {
                    Ok(fetch) => fetches.push(fetch),
                    Err(_) => break,
                }
            }
            let _ = response.send(fetches);
        });
        Ok(receiver)
    }
}

impl Drop for AccountRefreshWorker {
    fn drop(&mut self) {
        for worker in self.workers.values_mut() {
            worker.sender.take();
        }
        for worker in self.workers.values_mut() {
            if let Some(handle) = worker.handle.take() {
                join_with_deadline(handle, Duration::from_millis(100));
            }
        }
    }
}

fn join_with_deadline(handle: JoinHandle<()>, deadline: Duration) {
    let started = Instant::now();
    while !handle.is_finished() && started.elapsed() < deadline {
        thread::sleep(Duration::from_millis(1));
    }
    if handle.is_finished() {
        let _ = handle.join();
    }
}

pub(crate) fn try_receive(
    receiver: &Receiver<Vec<RefreshFetch>>,
) -> Result<Option<Vec<RefreshFetch>>, String> {
    match receiver.try_recv() {
        Ok(value) => Ok(Some(value)),
        Err(TryRecvError::Empty) => Ok(None),
        Err(TryRecvError::Disconnected) => Err("account refresh worker stopped".into()),
    }
}
