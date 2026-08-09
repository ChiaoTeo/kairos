use std::path::Path;
use std::time::Duration;

use kairos_transport::{SharedSnapshotReader, SharedSnapshotWriter};

use crate::{ContractError, ContractResult, SnapshotEnvelope};

pub struct UnixHttpClient {
    client: reqwest::blocking::Client,
}

impl UnixHttpClient {
    pub fn connect(socket: impl AsRef<Path>) -> ContractResult<Self> {
        let client = reqwest::blocking::Client::builder()
            .unix_socket(socket.as_ref().to_path_buf())
            .timeout(Duration::from_secs(3))
            .build()
            .map_err(|error| {
                ContractError::Transport(format!("build Unix HTTP client: {error}"))
            })?;
        Ok(Self { client })
    }

    pub fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&serde_json::Value>,
    ) -> ContractResult<serde_json::Value> {
        let method = method
            .parse()
            .map_err(|error| ContractError::Invalid(format!("invalid HTTP method: {error}")))?;
        let request = self
            .client
            .request(method, format!("http://localhost{path}"));
        let response = match body {
            Some(body) => request.json(body).send(),
            None => request.send(),
        }
        .map_err(|error| ContractError::Transport(format!("request {path}: {error}")))?;
        let status = response.status();
        let value: serde_json::Value = response.json().map_err(|error| {
            ContractError::Transport(format!("decode {path} response: {error}"))
        })?;
        if !status.is_success() {
            return Err(ContractError::Transport(format!(
                "request {path} failed with HTTP {status}: {value}"
            )));
        }
        Ok(value)
    }
}

pub struct MmapSnapshotPublisher {
    writer: SharedSnapshotWriter,
}

impl MmapSnapshotPublisher {
    pub fn create(path: impl AsRef<Path>, slot_size: usize) -> ContractResult<Self> {
        Ok(Self {
            writer: SharedSnapshotWriter::create(path, slot_size)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }

    pub fn publish(&mut self, snapshot: &SnapshotEnvelope) -> ContractResult<()> {
        self.writer
            .publish(snapshot.generation, &snapshot.payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct MmapSnapshotReader {
    reader: SharedSnapshotReader,
}

impl MmapSnapshotReader {
    pub fn open(path: impl AsRef<Path>) -> ContractResult<Self> {
        Ok(Self {
            reader: SharedSnapshotReader::open(path)
                .map_err(|error| ContractError::Transport(error.to_string()))?,
        })
    }

    pub fn read_payload(&self) -> ContractResult<kairos_transport::SharedSnapshotPayload> {
        self.reader.read_payload().map_err(ContractError::Transport)
    }
}
