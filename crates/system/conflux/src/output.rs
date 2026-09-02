use kairos_indexed_view::{EnvironmentOptions, IndexedViewIdentity, IndexedViewWriter, Mutation};
use kairos_transport::{AeronBytePublisher, AeronEndpoint};

use crate::{
    EnsureDisposition, NamedResources, ResourceError, ResourceOperationError, ResourceState,
};

#[derive(Clone, Debug)]
pub struct AeronOutputDeclaration {
    pub endpoint: AeronEndpoint,
    pub revision: u64,
}

#[derive(Clone, Debug)]
pub struct IndexedOutputDeclaration {
    pub options: EnvironmentOptions,
    pub identity: IndexedViewIdentity,
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

pub struct IndexedOutputs<'a> {
    resources: &'a mut NamedResources<String, IndexedViewWriter>,
}

/// Borrowed output capabilities owned by one Conflux process.
pub struct OutputCollections<'a> {
    pub aeron: AeronOutputs<'a>,
    pub indexed: IndexedOutputs<'a>,
}

impl<'a> OutputCollections<'a> {
    pub(crate) fn new(
        aeron: &'a mut NamedResources<String, AeronBytePublisher>,
        indexed: &'a mut NamedResources<String, IndexedViewWriter>,
    ) -> Self {
        Self {
            aeron: AeronOutputs { resources: aeron },
            indexed: IndexedOutputs { resources: indexed },
        }
    }
}

impl IndexedOutputs<'_> {
    pub fn declare(
        &mut self,
        key: impl Into<String>,
        declaration: IndexedOutputDeclaration,
    ) -> Result<EnsureDisposition, OutputCreateError> {
        let key = key.into();
        declare(self.resources, key, declaration.revision, || {
            IndexedViewWriter::create(&declaration.options, declaration.identity)
                .map_err(|error| OutputCreateError::Create(error.to_string()))
        })
    }

    pub fn apply(
        &mut self,
        key: &str,
        mutations: &[Mutation],
        applied_event_sequence: u64,
        committed_at_unix_nanos: u64,
    ) -> Result<(), OutputPublishError> {
        publish_with(self.resources, key, |writer| {
            writer
                .apply(mutations, applied_event_sequence, committed_at_unix_nanos)
                .map_err(|error| error.to_string())
        })
    }

    pub fn contains(&self, key: &str) -> bool {
        self.resources.get(&key.to_owned()).is_some()
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
    let (disposition, resource) = resources.ensure_with_entry(key, revision, || resource)?;
    resource.set_state(ResourceState::Ready);
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
