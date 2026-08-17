//! Application-facing loading of historical replay datasets.

use std::path::{Path, PathBuf};

use crate::domain::observation::MarketObservation;

#[derive(Clone, Debug, serde::Deserialize)]
struct ReplayManifest {
    path: PathBuf,
    event_count: usize,
}

/// Loads a JSONL replay source, optionally through its adjacent manifest.
///
/// Passing either `events.jsonl` or `events.manifest.json` is supported. When
/// a manifest exists, the event count is checked before the replay starts so
/// a truncated download cannot silently become a valid backtest input.
pub fn load_replay_events(path: impl AsRef<Path>) -> Result<Vec<MarketObservation>, String> {
    let requested = path.as_ref();
    let manifest_path = if requested
        .file_name()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.ends_with(".manifest.json"))
    {
        Some(requested.to_owned())
    } else {
        let candidate = requested.with_extension("manifest.json");
        candidate.is_file().then_some(candidate)
    };
    let data_path = if let Some(manifest_path) = manifest_path {
        let manifest: ReplayManifest = serde_json::from_slice(
            &std::fs::read(&manifest_path).map_err(|error| error.to_string())?,
        )
        .map_err(|error| format!("invalid replay manifest: {error}"))?;
        if manifest.path.as_os_str().is_empty() {
            return Err("replay manifest path is empty".into());
        }
        let data_path = if manifest.path.is_absolute() {
            manifest.path
        } else {
            manifest_path
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .join(manifest.path)
        };
        let content = std::fs::read_to_string(&data_path).map_err(|error| error.to_string())?;
        let events = parse_jsonl(&content)?;
        if events.len() != manifest.event_count {
            return Err(format!(
                "replay dataset event count mismatch: manifest={}, file={}",
                manifest.event_count,
                events.len()
            ));
        }
        return Ok(events);
    } else {
        requested.to_owned()
    };
    parse_jsonl(&std::fs::read_to_string(data_path).map_err(|error| error.to_string())?)
}

/// Loads several independently validated datasets while retaining each
/// observation's source identity for a combined replay.
pub fn load_replay_events_many(
    paths: impl IntoIterator<Item = impl AsRef<Path>>,
) -> Result<Vec<MarketObservation>, String> {
    let mut events = Vec::new();
    for path in paths {
        events.extend(load_replay_events(path)?);
    }
    Ok(events)
}

fn parse_jsonl(content: &str) -> Result<Vec<MarketObservation>, String> {
    content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).map_err(|error| error.to_string()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::load_replay_events;
    use crate::domain::observation::{Bar, MarketObservation};

    #[test]
    fn manifest_replay_rejects_truncated_file() {
        let directory = tempfile::tempdir().unwrap();
        let data = directory.path().join("events.jsonl");
        let manifest = directory.path().join("events.manifest.json");
        let event = MarketObservation::Bar(Bar {
            market_id: kairos_primitives::MarketId::new("market:test").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:test").unwrap(),
            timeframe: "1m".into(),
            open: "1".parse().unwrap(),
            high: "1".parse().unwrap(),
            low: "1".parse().unwrap(),
            close: "1".parse().unwrap(),
            volume: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
            source_id: "test".into(),
            derivation: "test".into(),
        });
        std::fs::write(&data, serde_json::to_string(&event).unwrap()).unwrap();
        std::fs::write(
            &manifest,
            serde_json::json!({"path":"events.jsonl", "event_count":2}).to_string(),
        )
        .unwrap();
        assert!(load_replay_events(&data).is_err());
    }
}
