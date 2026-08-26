use std::collections::BTreeMap;

use crate::StoreError;

pub const FORMAT_VERSION: u32 = 3;
pub(crate) const METADATA_DATABASE: &str = "__kairos_metadata";
pub(crate) const KEY_IDENTITY: &[u8] = b"identity";
pub(crate) const KEY_FORMAT_VERSION: &[u8] = b"format_version";
pub(crate) const KEY_SCHEMA_SET: &[u8] = b"schema_set";
pub(crate) const KEY_RESOURCE_EPOCH: &[u8] = b"resource_epoch";
pub(crate) const KEY_PRODUCER_INCARNATION: &[u8] = b"producer_incarnation";
pub(crate) const KEY_APPLIED_EVENT_SEQUENCE: &[u8] = b"applied_event_sequence";
pub(crate) const KEY_COMMITTED_AT_UNIX_NANOS: &[u8] = b"committed_at_unix_nanos";
pub(crate) const KEY_REBUILD_STATE: &[u8] = b"rebuild_state";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SchemaDescriptor {
    pub database: String,
    pub key_version: u32,
    pub value_schema: String,
    pub value_version: u32,
}

impl SchemaDescriptor {
    pub fn new(
        database: impl Into<String>,
        key_version: u32,
        value_schema: impl Into<String>,
        value_version: u32,
    ) -> Result<Self, StoreError> {
        let descriptor = Self {
            database: database.into(),
            key_version,
            value_schema: value_schema.into(),
            value_version,
        };
        validate_text("database", &descriptor.database)?;
        validate_text("value schema", &descriptor.value_schema)?;
        if descriptor.database == METADATA_DATABASE {
            return Err(StoreError::InvalidSchema(
                "the reserved metadata database cannot be owner-declared".into(),
            ));
        }
        if descriptor.key_version == 0 || descriptor.value_version == 0 {
            return Err(StoreError::InvalidSchema(
                "key and value schema versions must be positive".into(),
            ));
        }
        Ok(descriptor)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SchemaSet(Vec<SchemaDescriptor>);

impl SchemaSet {
    pub fn new(schemas: impl IntoIterator<Item = SchemaDescriptor>) -> Result<Self, StoreError> {
        let mut by_name = BTreeMap::new();
        for schema in schemas {
            if by_name.insert(schema.database.clone(), schema).is_some() {
                return Err(StoreError::InvalidSchema(
                    "named databases must be unique".into(),
                ));
            }
        }
        Ok(Self(by_name.into_values().collect()))
    }

    pub fn descriptors(&self) -> &[SchemaDescriptor] {
        &self.0
    }

    pub(crate) fn encode(&self) -> Result<Vec<u8>, StoreError> {
        let mut output = Vec::new();
        output.extend_from_slice(b"KIS3");
        put_u32(
            &mut output,
            self.0.len().try_into().map_err(|_| {
                StoreError::InvalidSchema("schema set contains too many databases".into())
            })?,
        );
        for schema in &self.0 {
            put_text(&mut output, &schema.database)?;
            put_u32(&mut output, schema.key_version);
            put_text(&mut output, &schema.value_schema)?;
            put_u32(&mut output, schema.value_version);
        }
        Ok(output)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IndexedViewIdentity {
    pub workspace_id: String,
    pub launch_id: Option<String>,
    pub instance_id: Option<String>,
    pub owner: String,
    pub publisher_resource_id: String,
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub schema_set: SchemaSet,
}

impl IndexedViewIdentity {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        workspace_id: impl Into<String>,
        launch_id: Option<impl Into<String>>,
        instance_id: Option<impl Into<String>>,
        owner: impl Into<String>,
        publisher_resource_id: impl Into<String>,
        resource_epoch: u64,
        producer_incarnation: u64,
        schema_set: SchemaSet,
    ) -> Result<Self, StoreError> {
        let identity = Self {
            workspace_id: workspace_id.into(),
            launch_id: launch_id.map(Into::into),
            instance_id: instance_id.map(Into::into),
            owner: owner.into(),
            publisher_resource_id: publisher_resource_id.into(),
            resource_epoch,
            producer_incarnation,
            schema_set,
        };
        validate_text("workspace id", &identity.workspace_id)?;
        validate_optional_text("launch id", identity.launch_id.as_deref())?;
        validate_optional_text("instance id", identity.instance_id.as_deref())?;
        validate_text("owner", &identity.owner)?;
        validate_text("publisher resource id", &identity.publisher_resource_id)?;
        if identity.resource_epoch == 0 || identity.producer_incarnation == 0 {
            return Err(StoreError::InvalidIdentity(
                "resource epoch and producer incarnation must be positive".into(),
            ));
        }
        Ok(identity)
    }

    pub(crate) fn encode_identity(&self) -> Result<Vec<u8>, StoreError> {
        let mut output = Vec::new();
        output.extend_from_slice(b"KIV3");
        put_text(&mut output, &self.workspace_id)?;
        put_optional_text(&mut output, self.launch_id.as_deref())?;
        put_optional_text(&mut output, self.instance_id.as_deref())?;
        put_text(&mut output, &self.owner)?;
        put_text(&mut output, &self.publisher_resource_id)?;
        Ok(output)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RebuildState {
    Building,
    Ready,
    Failed { diagnostic_code: String },
}

impl RebuildState {
    pub(crate) fn encode(&self) -> Result<Vec<u8>, StoreError> {
        let mut output = Vec::new();
        match self {
            Self::Building => output.push(1),
            Self::Ready => output.push(2),
            Self::Failed { diagnostic_code } => {
                output.push(3);
                put_text(&mut output, diagnostic_code)?;
            },
        }
        Ok(output)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MetadataSnapshot {
    pub format_version: u32,
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub applied_event_sequence: u64,
    pub committed_at_unix_nanos: u64,
    pub rebuild_state: RebuildState,
}

pub(crate) fn encode_u32(value: u32) -> [u8; 4] {
    value.to_be_bytes()
}

pub(crate) fn encode_u64(value: u64) -> [u8; 8] {
    value.to_be_bytes()
}

pub(crate) fn decode_u32(bytes: &[u8], field: &str) -> Result<u32, StoreError> {
    Ok(u32::from_be_bytes(bytes.try_into().map_err(|_| {
        StoreError::CorruptMetadata(format!("{field} must contain exactly four bytes"))
    })?))
}

pub(crate) fn decode_u64(bytes: &[u8], field: &str) -> Result<u64, StoreError> {
    Ok(u64::from_be_bytes(bytes.try_into().map_err(|_| {
        StoreError::CorruptMetadata(format!("{field} must contain exactly eight bytes"))
    })?))
}

pub(crate) fn decode_rebuild_state(bytes: &[u8]) -> Result<RebuildState, StoreError> {
    match bytes.first().copied() {
        Some(1) if bytes.len() == 1 => Ok(RebuildState::Building),
        Some(2) if bytes.len() == 1 => Ok(RebuildState::Ready),
        Some(3) => {
            let mut cursor = &bytes[1..];
            let diagnostic_code = take_text(&mut cursor)?;
            if !cursor.is_empty() {
                return Err(StoreError::CorruptMetadata(
                    "failed rebuild state has trailing bytes".into(),
                ));
            }
            Ok(RebuildState::Failed { diagnostic_code })
        },
        _ => Err(StoreError::CorruptMetadata(
            "invalid rebuild state encoding".into(),
        )),
    }
}

fn validate_optional_text(field: &str, value: Option<&str>) -> Result<(), StoreError> {
    if let Some(value) = value {
        validate_text(field, value)?;
    }
    Ok(())
}

fn validate_text(field: &str, value: &str) -> Result<(), StoreError> {
    if value.is_empty() || value.trim() != value || value.as_bytes().contains(&0) {
        return Err(StoreError::InvalidIdentity(format!(
            "{field} must be non-empty, trimmed UTF-8 without NUL"
        )));
    }
    if value.len() > u16::MAX as usize {
        return Err(StoreError::InvalidIdentity(format!(
            "{field} exceeds the u16 encoded length"
        )));
    }
    Ok(())
}

fn put_optional_text(output: &mut Vec<u8>, value: Option<&str>) -> Result<(), StoreError> {
    match value {
        Some(value) => {
            output.push(1);
            put_text(output, value)
        },
        None => {
            output.push(0);
            Ok(())
        },
    }
}

fn put_text(output: &mut Vec<u8>, value: &str) -> Result<(), StoreError> {
    let length: u16 = value
        .len()
        .try_into()
        .map_err(|_| StoreError::InvalidIdentity("metadata text is too long".into()))?;
    output.extend_from_slice(&length.to_be_bytes());
    output.extend_from_slice(value.as_bytes());
    Ok(())
}

fn put_u32(output: &mut Vec<u8>, value: u32) {
    output.extend_from_slice(&value.to_be_bytes());
}

fn take_text(input: &mut &[u8]) -> Result<String, StoreError> {
    if input.len() < 2 {
        return Err(StoreError::CorruptMetadata(
            "metadata text is missing its length".into(),
        ));
    }
    let length = u16::from_be_bytes([input[0], input[1]]) as usize;
    *input = &input[2..];
    if input.len() < length {
        return Err(StoreError::CorruptMetadata(
            "metadata text is truncated".into(),
        ));
    }
    let value = std::str::from_utf8(&input[..length])
        .map_err(|error| StoreError::CorruptMetadata(error.to_string()))?
        .to_owned();
    *input = &input[length..];
    Ok(value)
}
