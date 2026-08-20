use std::path::PathBuf;

pub use kairos_transport::SnapshotEnvelopeMetadata;
use kairos_transport::{
    AeronBytePublisher, AeronEndpoint, AtomicFileSnapshotStorage, SharedSnapshotWriter,
};

use crate::{
    EnsureDisposition, NamedResources, ResourceError, ResourceOperationError, ResourceState,
};

#[derive(Clone, Debug)]
pub struct AeronOutputDeclaration {
    pub endpoint: AeronEndpoint,
    pub revision: u64,
}

#[derive(Clone, Debug)]
pub struct MmapOutputDeclaration {
    pub path: PathBuf,
    pub slot_capacity: usize,
    pub revision: u64,
}

#[derive(Clone, Debug)]
pub struct FileOutputDeclaration {
    pub path: PathBuf,
    pub max_payload_len: usize,
    pub revision: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum OutputCreateError {
    #[error("create output: {0}")]
    Create(String),
    #[error(
        "output `{key}` already exists at revision {current}; received revision {received} requires retire-and-recreate"
    )]
    AlreadyExists {
        key: String,
        current: u64,
        received: u64,
    },
    #[error(transparent)]
    Resource(#[from] ResourceError),
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum OutputPublishError {
    #[error("output `{0}` does not exist")]
    NotFound(String),
    #[error("publish output `{key}`: {error}")]
    Publish { key: String, error: String },
}

pub struct AeronOutputs<'a> {
    resources: &'a mut NamedResources<String, AeronBytePublisher>,
}

pub struct MmapOutputs<'a> {
    resources: &'a mut NamedResources<String, SharedSnapshotWriter>,
}

pub struct FileOutputs<'a> {
    resources: &'a mut NamedResources<String, AtomicFileSnapshotStorage>,
}

/// Borrowed output capabilities owned by one Conflux process.
pub struct OutputCollections<'a> {
    pub aeron: AeronOutputs<'a>,
    pub mmap: MmapOutputs<'a>,
    pub file: FileOutputs<'a>,
}

impl<'a> OutputCollections<'a> {
    pub(crate) fn new(
        aeron: &'a mut NamedResources<String, AeronBytePublisher>,
        mmap: &'a mut NamedResources<String, SharedSnapshotWriter>,
        file: &'a mut NamedResources<String, AtomicFileSnapshotStorage>,
    ) -> Self {
        Self {
            aeron: AeronOutputs { resources: aeron },
            mmap: MmapOutputs { resources: mmap },
            file: FileOutputs { resources: file },
        }
    }
}

impl AeronOutputs<'_> {
    pub fn declare(
        &mut self,
        key: impl Into<String>,
        declaration: AeronOutputDeclaration,
    ) -> Result<EnsureDisposition, OutputCreateError> {
        let key = key.into();
        declare(self.resources, key, declaration.revision, || {
            AeronBytePublisher::connect_endpoint(&declaration.endpoint)
                .map_err(|error| OutputCreateError::Create(error.to_string()))
        })
    }

    pub fn publish(&mut self, key: &str, payload: &[u8]) -> Result<(), OutputPublishError> {
        publish_with(self.resources, key, |publisher| {
            publisher
                .publish(payload)
                .map(|_| ())
                .map_err(|error| error.to_string())
        })
    }

    pub fn contains(&self, key: &str) -> bool {
        self.resources.get(&key.to_owned()).is_some()
    }
}

impl MmapOutputs<'_> {
    pub fn declare(
        &mut self,
        key: impl Into<String>,
        declaration: MmapOutputDeclaration,
    ) -> Result<EnsureDisposition, OutputCreateError> {
        let key = key.into();
        declare(self.resources, key, declaration.revision, || {
            SharedSnapshotWriter::create(declaration.path, declaration.slot_capacity)
                .map_err(|error| OutputCreateError::Create(error.to_string()))
        })
    }

    pub fn publish(
        &mut self,
        key: &str,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> Result<(), OutputPublishError> {
        publish_with(self.resources, key, |writer| {
            writer
                .publish_with_metadata(metadata, payload)
                .map_err(|error| error.to_string())
        })
    }

    pub fn contains(&self, key: &str) -> bool {
        self.resources.get(&key.to_owned()).is_some()
    }
}

impl FileOutputs<'_> {
    pub fn declare(
        &mut self,
        key: impl Into<String>,
        declaration: FileOutputDeclaration,
    ) -> Result<EnsureDisposition, OutputCreateError> {
        let key = key.into();
        declare(self.resources, key, declaration.revision, || {
            AtomicFileSnapshotStorage::create(declaration.path, declaration.max_payload_len)
                .map_err(|error| OutputCreateError::Create(error.to_string()))
        })
    }

    pub fn publish(
        &mut self,
        key: &str,
        metadata: SnapshotEnvelopeMetadata,
        payload: &[u8],
    ) -> Result<(), OutputPublishError> {
        publish_with(self.resources, key, |writer| {
            writer
                .publish(metadata, payload)
                .map(|_| ())
                .map_err(|error| error.to_string())
        })
    }

    pub fn contains(&self, key: &str) -> bool {
        self.resources.get(&key.to_owned()).is_some()
    }
}

fn declare<R>(
    resources: &mut NamedResources<String, R>,
    key: String,
    revision: u64,
    create: impl FnOnce() -> Result<R, OutputCreateError>,
) -> Result<EnsureDisposition, OutputCreateError> {
    if let Some(resource) = resources.get(&key) {
        if revision < resource.revision() {
            return Err(ResourceError::StaleRevision {
                current: resource.revision(),
                received: revision,
            }
            .into());
        }
        if revision == resource.revision() {
            return Ok(EnsureDisposition::Existing);
        }
        return Err(OutputCreateError::AlreadyExists {
            key,
            current: resource.revision(),
            received: revision,
        });
    }
    let resource = create()?;
    let disposition = resources.ensure_with(key.clone(), revision, || resource)?;
    resources
        .get_mut(&key)
        .expect("declared output exists")
        .set_state(ResourceState::Ready);
    Ok(disposition)
}

fn publish_with<R>(
    resources: &mut NamedResources<String, R>,
    key: &str,
    publish: impl FnOnce(&mut R) -> Result<(), String>,
) -> Result<(), OutputPublishError> {
    resources
        .try_with(&key.to_owned(), publish)
        .map_err(|error| match error {
            ResourceOperationError::NotFound => OutputPublishError::NotFound(key.to_owned()),
            ResourceOperationError::Operation(error) => OutputPublishError::Publish {
                key: key.to_owned(),
                error,
            },
        })
}

#[cfg(test)]
mod tests {
    use kairos_transport::SharedSnapshotReader;

    use super::*;

    #[test]
    fn mmap_pipe_is_created_owned_and_published_through_the_facade() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("view.mmap");
        let mut resources = NamedResources::new();
        let mut outputs = MmapOutputs {
            resources: &mut resources,
        };
        let declaration = MmapOutputDeclaration {
            path: path.clone(),
            slot_capacity: 4_096,
            revision: 1,
        };

        assert_eq!(
            outputs.declare("view", declaration.clone()).unwrap(),
            EnsureDisposition::Created
        );
        assert_eq!(
            outputs.declare("view", declaration).unwrap(),
            EnsureDisposition::Existing
        );
        outputs
            .publish(
                "view",
                SnapshotEnvelopeMetadata {
                    resource_epoch: 1,
                    producer_incarnation: 2,
                    generation: 3,
                    applied_event_sequence: 3,
                    published_at_unix_nanos: 4,
                },
                b"typed-payload",
            )
            .unwrap();

        let snapshot = SharedSnapshotReader::open(path)
            .unwrap()
            .read_payload()
            .unwrap();
        assert_eq!(snapshot.payload, b"typed-payload");
        assert_eq!(
            resources.get(&"view".to_owned()).unwrap().state(),
            ResourceState::Ready
        );
    }
}
