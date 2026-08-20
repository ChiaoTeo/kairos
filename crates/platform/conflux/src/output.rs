use std::path::PathBuf;

use kairos_transport::{
    AeronBytePublisher, AeronEndpoint, AtomicFileSnapshotStorage, SharedSnapshotWriter,
    SnapshotEnvelopeMetadata,
};

use crate::{EnsureDisposition, NamedResources, ResourceError, ResourceOperationError, ResourceState};

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
            publisher.publish(payload).map(|_| ()).map_err(|error| error.to_string())
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
    if resources
        .get(&key)
        .is_some_and(|resource| resource.revision() == revision)
    {
        return Ok(EnsureDisposition::Existing);
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
