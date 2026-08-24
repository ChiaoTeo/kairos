//! Composition-owned crash-recoverable JSONL history recording.

use std::path::{Path, PathBuf};

use serde::Serialize;
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;

use crate::services::publication::HistoryQueue;
use crate::{MarketEvent, MarketObservation};

pub(crate) fn spawn_jsonl_history(
    specs: Vec<HistoryCollectionSpec>,
) -> Result<HistoryQueue, String> {
    let mut recorder = JsonlMarketHistoryRecorder::spawn(specs)?;
    let (sender, mut receiver) = mpsc::channel::<Vec<(u64, MarketEvent)>>(64);
    let task = tokio::spawn(async move {
        while let Some(events) = receiver.recv().await {
            recorder.record(&events).await?;
        }
        recorder.shutdown().await
    });
    Ok(HistoryQueue::new(sender, task))
}

#[derive(Clone, Debug)]
pub(crate) struct HistoryCollectionSpec {
    pub name: String,
    pub scope_key: String,
    pub selectors: Vec<String>,
    pub root: PathBuf,
    pub queue_capacity: usize,
}

struct CollectionWorker {
    spec: HistoryCollectionSpec,
    sender: Option<mpsc::Sender<HistoryRecord>>,
    task: Option<tokio::task::JoinHandle<Result<(), String>>>,
}

#[derive(Clone)]
struct HistoryRecord {
    sequence: u64,
    observation: MarketObservation,
}

pub(crate) struct JsonlMarketHistoryRecorder {
    workers: Vec<CollectionWorker>,
}

impl JsonlMarketHistoryRecorder {
    pub(crate) fn spawn(specs: Vec<HistoryCollectionSpec>) -> Result<Self, String> {
        let mut workers = Vec::with_capacity(specs.len());
        for spec in specs {
            if spec.name.trim().is_empty() || spec.scope_key.trim().is_empty() {
                return Err("history collection name and scope_key are required".into());
            }
            if spec.queue_capacity == 0 {
                return Err(format!(
                    "history collection {} queue_capacity must be positive",
                    spec.name
                ));
            }
            std::fs::create_dir_all(&spec.root).map_err(|error| {
                format!(
                    "create history collection directory {}: {error}",
                    spec.root.display()
                )
            })?;
            let (sender, receiver) = mpsc::channel(spec.queue_capacity);
            let worker_spec = spec.clone();
            let task = tokio::spawn(async move { run_collection(worker_spec, receiver).await });
            workers.push(CollectionWorker {
                spec,
                sender: Some(sender),
                task: Some(task),
            });
        }
        Ok(Self { workers })
    }
}

impl JsonlMarketHistoryRecorder {
    pub(crate) async fn record(&mut self, events: &[(u64, MarketEvent)]) -> Result<(), String> {
        for (sequence, event) in events {
            let MarketEvent::Observation(observation) = event else {
                continue;
            };
            for worker in &self.workers {
                if observation.scope().key() != worker.spec.scope_key
                    || !selector_matches(&worker.spec.selectors, observation)
                {
                    continue;
                }
                worker
                    .sender
                    .as_ref()
                    .ok_or_else(|| {
                        format!("history collection {} is shutting down", worker.spec.name)
                    })?
                    .send(HistoryRecord {
                        sequence: *sequence,
                        observation: observation.clone(),
                    })
                    .await
                    .map_err(|_| {
                        format!("history collection {} writer stopped", worker.spec.name)
                    })?;
            }
        }
        Ok(())
    }

    pub(crate) async fn shutdown(&mut self) -> Result<(), String> {
        for worker in &mut self.workers {
            worker.sender.take();
        }
        for worker in &mut self.workers {
            let Some(task) = worker.task.take() else {
                continue;
            };
            task.await.map_err(|error| {
                format!(
                    "history collection {} join failed: {error}",
                    worker.spec.name
                )
            })??;
        }
        Ok(())
    }
}

fn selector_matches(selectors: &[String], observation: &MarketObservation) -> bool {
    selectors.is_empty()
        || selectors.iter().any(|selector| {
            let (kind, qualifier) = selector
                .split_once(':')
                .map(|(kind, qualifier)| (kind, Some(qualifier)))
                .unwrap_or((selector.as_str(), None));
            kind.eq_ignore_ascii_case(observation.kind().as_str())
                && qualifier.is_none_or(|value| {
                    observation
                        .qualifier()
                        .is_some_and(|actual| actual.eq_ignore_ascii_case(value))
                })
        })
}

#[derive(Serialize)]
struct CollectionManifest<'a> {
    schema_version: u16,
    name: &'a str,
    format: &'static str,
    path: String,
    scope_key: &'a str,
    selectors: &'a [String],
    event_count: u64,
    first_event_time_unix_nanos: Option<u64>,
    last_event_time_unix_nanos: Option<u64>,
    last_market_sequence: Option<u64>,
}

async fn run_collection(
    spec: HistoryCollectionSpec,
    mut receiver: mpsc::Receiver<HistoryRecord>,
) -> Result<(), String> {
    let data_path = spec.root.join("events.jsonl");
    let (mut event_count, mut first_time, mut last_time) = recover_file(&data_path)?;
    let mut last_sequence = recover_manifest_sequence(&spec.root.join("manifest.json"))?;
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&data_path)
        .await
        .map_err(|error| format!("open history file {}: {error}", data_path.display()))?;
    while let Some(record) = receiver.recv().await {
        let event_time = record.observation.observed_at_unix_nanos().get();
        let mut encoded = serde_json::to_vec(&record.observation)
            .map_err(|error| format!("encode history observation: {error}"))?;
        encoded.push(b'\n');
        file.write_all(&encoded)
            .await
            .map_err(|error| format!("append history file {}: {error}", data_path.display()))?;
        event_count = event_count.saturating_add(1);
        first_time = Some(first_time.map_or(event_time, |value| value.min(event_time)));
        last_time = Some(last_time.map_or(event_time, |value| value.max(event_time)));
        last_sequence = Some(record.sequence);
    }
    file.flush()
        .await
        .map_err(|error| format!("flush history file {}: {error}", data_path.display()))?;
    file.sync_data()
        .await
        .map_err(|error| format!("sync history file {}: {error}", data_path.display()))?;
    write_manifest(
        &spec,
        &data_path,
        event_count,
        first_time,
        last_time,
        last_sequence,
    )
    .await
}

fn recover_file(path: &Path) -> Result<(u64, Option<u64>, Option<u64>), String> {
    let input = match std::fs::read_to_string(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok((0, None, None));
        },
        Err(error) => return Err(format!("read history file {}: {error}", path.display())),
    };
    let mut count = 0_u64;
    let mut first: Option<u64> = None;
    let mut last: Option<u64> = None;
    for (index, line) in input.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let observation: MarketObservation = serde_json::from_str(line).map_err(|error| {
            format!(
                "history file {} has invalid line {}: {error}",
                path.display(),
                index + 1
            )
        })?;
        let event_time = observation.observed_at_unix_nanos().get();
        count = count.saturating_add(1);
        first = Some(first.map_or(event_time, |value| value.min(event_time)));
        last = Some(last.map_or(event_time, |value| value.max(event_time)));
    }
    Ok((count, first, last))
}

fn recover_manifest_sequence(path: &Path) -> Result<Option<u64>, String> {
    let input = match std::fs::read(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("read history manifest {}: {error}", path.display())),
    };
    let value: serde_json::Value = serde_json::from_slice(&input)
        .map_err(|error| format!("decode history manifest {}: {error}", path.display()))?;
    Ok(value
        .get("last_market_sequence")
        .and_then(serde_json::Value::as_u64))
}

async fn write_manifest(
    spec: &HistoryCollectionSpec,
    data_path: &Path,
    event_count: u64,
    first_time: Option<u64>,
    last_time: Option<u64>,
    last_sequence: Option<u64>,
) -> Result<(), String> {
    let manifest = CollectionManifest {
        schema_version: 2,
        name: &spec.name,
        format: "jsonl",
        path: data_path.display().to_string(),
        scope_key: &spec.scope_key,
        selectors: &spec.selectors,
        event_count,
        first_event_time_unix_nanos: first_time,
        last_event_time_unix_nanos: last_time,
        last_market_sequence: last_sequence,
    };
    let manifest_path = spec.root.join("manifest.json");
    let temporary = spec.root.join("manifest.tmp");
    let mut encoded = serde_json::to_vec_pretty(&manifest)
        .map_err(|error| format!("encode history manifest: {error}"))?;
    encoded.push(b'\n');
    tokio::fs::write(&temporary, encoded)
        .await
        .map_err(|error| format!("write history manifest {}: {error}", temporary.display()))?;
    tokio::fs::rename(&temporary, &manifest_path)
        .await
        .map_err(|error| {
            format!(
                "install history manifest {}: {error}",
                manifest_path.display()
            )
        })
}

#[cfg(test)]
mod tests {
    use super::{HistoryCollectionSpec, JsonlMarketHistoryRecorder};
    use crate::{MarketEvent, MarketObservation, Quote};

    fn quote(time: u64) -> MarketObservation {
        MarketObservation::Quote(Quote {
            scope: crate::ObservationScope::market("market:btc").unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:btc")
                .unwrap(),
            bid_price: Some("100".parse().unwrap()),
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            bid_venue_code: None,
            ask_venue_code: None,
            tape: None,
            observed_at_unix_nanos: kairos_primitives::time::UnixNanos::new(time),
            provider: kairos_primitives::market::Provider::new("binance").unwrap(),
        })
    }

    #[tokio::test]
    async fn recorder_recovers_append_log_and_writes_manifest() {
        let root = tempfile::tempdir().unwrap();
        let spec = HistoryCollectionSpec {
            name: "btc-quotes".into(),
            scope_key: "market:btc".into(),
            selectors: vec!["quote".into()],
            root: root.path().join("btc-quotes"),
            queue_capacity: 2,
        };
        for (sequence, time) in [(1, 10), (2, 20)] {
            let mut recorder = JsonlMarketHistoryRecorder::spawn(vec![spec.clone()]).unwrap();
            recorder
                .record(&[(sequence, MarketEvent::Observation(quote(time)))])
                .await
                .unwrap();
            recorder.shutdown().await.unwrap();
        }
        let mut recorder = JsonlMarketHistoryRecorder::spawn(vec![spec.clone()]).unwrap();
        recorder.shutdown().await.unwrap();
        let manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(spec.root.join("manifest.json")).unwrap())
                .unwrap();
        assert_eq!(manifest["event_count"], 2);
        assert_eq!(manifest["first_event_time_unix_nanos"], 10);
        assert_eq!(manifest["last_event_time_unix_nanos"], 20);
        assert_eq!(manifest["last_market_sequence"], 2);
        assert_eq!(
            std::fs::read_to_string(spec.root.join("events.jsonl"))
                .unwrap()
                .lines()
                .count(),
            2
        );
    }

    #[tokio::test]
    async fn recorder_rejects_a_corrupted_existing_log() {
        let root = tempfile::tempdir().unwrap();
        let collection_root = root.path().join("corrupt");
        std::fs::create_dir_all(&collection_root).unwrap();
        std::fs::write(collection_root.join("events.jsonl"), b"not-json\n").unwrap();
        let mut recorder = JsonlMarketHistoryRecorder::spawn(vec![HistoryCollectionSpec {
            name: "corrupt".into(),
            scope_key: "market:btc".into(),
            selectors: vec![],
            root: collection_root,
            queue_capacity: 2,
        }])
        .unwrap();

        let error = recorder.shutdown().await.unwrap_err();

        assert!(error.contains("invalid line 1"));
    }
}
