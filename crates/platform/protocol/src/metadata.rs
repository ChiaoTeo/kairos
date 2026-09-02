//! Common FlatBuffers metadata builders.

use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::{EventId, InstanceId, LaunchId, ProducerId, WorkspaceId};
use kairos_primitives::time::{Sequence, UnixNanos};

use crate::context::{EventProtocolContext, ViewProtocolContext};
use crate::generated::kairos::common::v_2::{
    EventMetadata, EventMetadataArgs, ViewCompleteness, ViewMetadata, ViewMetadataArgs,
};

/// Owned, validated process metadata returned by business contract decoders.
///
/// Generated FlatBuffers tables borrow their input buffer and must not cross a
/// language or process adapter. Business contracts decode their owner payloads
/// together with this common metadata value and can then safely expose it
/// through Pyo3 or another owned boundary.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventMetadataOwned {
    pub event_id: EventId,
    pub stream_id: String,
    pub sequence: Sequence,
    pub producer_id: ProducerId,
    pub producer_incarnation: u64,
    pub workspace_id: WorkspaceId,
    pub launch_id: Option<LaunchId>,
    pub instance_id: Option<InstanceId>,
    pub correlation_id: Option<String>,
    pub causation_id: Option<String>,
    pub occurred_at_unix_nanos: UnixNanos,
    pub published_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum EventMetadataDecodeError {
    #[error("event metadata field `{field}` is empty or not trimmed")]
    InvalidText { field: &'static str },
    #[error("event metadata sequence must be positive")]
    InvalidSequence,
    #[error("invalid event metadata field `{field}`: {reason}")]
    InvalidIdentity { field: &'static str, reason: String },
}

pub fn decode_event_metadata(
    value: EventMetadata<'_>,
) -> Result<EventMetadataOwned, EventMetadataDecodeError> {
    let event_id = EventId::new(value.event_id()).map_err(|error| {
        EventMetadataDecodeError::InvalidIdentity {
            field: "event_id",
            reason: error.to_string(),
        }
    })?;
    let stream_id = required_text(value.stream_id(), "stream_id")?.to_owned();
    if value.sequence() == 0 {
        return Err(EventMetadataDecodeError::InvalidSequence);
    }
    let producer_id = ProducerId::new(value.producer_id()).map_err(|error| {
        EventMetadataDecodeError::InvalidIdentity {
            field: "producer_id",
            reason: error.to_string(),
        }
    })?;
    let workspace_id = WorkspaceId::new(value.workspace_id()).map_err(|error| {
        EventMetadataDecodeError::InvalidIdentity {
            field: "workspace_id",
            reason: error.to_string(),
        }
    })?;
    let launch_id = optional_identity(value.launch_id(), "launch_id", LaunchId::new)?;
    let instance_id = optional_identity(value.instance_id(), "instance_id", InstanceId::new)?;
    Ok(EventMetadataOwned {
        event_id,
        stream_id,
        sequence: Sequence::new(value.sequence()),
        producer_id,
        producer_incarnation: value.producer_incarnation(),
        workspace_id,
        launch_id,
        instance_id,
        correlation_id: optional_text(value.correlation_id(), "correlation_id")?,
        causation_id: optional_text(value.causation_id(), "causation_id")?,
        occurred_at_unix_nanos: UnixNanos::new(value.occurred_at_unix_nanos()),
        published_at_unix_nanos: UnixNanos::new(value.published_at_unix_nanos()),
    })
}

fn required_text<'a>(
    value: &'a str,
    field: &'static str,
) -> Result<&'a str, EventMetadataDecodeError> {
    if value.is_empty() || value.trim() != value {
        return Err(EventMetadataDecodeError::InvalidText { field });
    }
    Ok(value)
}

fn optional_text(
    value: Option<&str>,
    field: &'static str,
) -> Result<Option<String>, EventMetadataDecodeError> {
    value
        .map(|value| required_text(value, field).map(ToOwned::to_owned))
        .transpose()
}

fn optional_identity<T>(
    value: Option<&str>,
    field: &'static str,
    constructor: impl FnOnce(String) -> Result<T, kairos_primitives::DomainTypeError>,
) -> Result<Option<T>, EventMetadataDecodeError> {
    value
        .map(|value| {
            constructor(value.to_owned()).map_err(|error| {
                EventMetadataDecodeError::InvalidIdentity {
                    field,
                    reason: error.to_string(),
                }
            })
        })
        .transpose()
}

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EventProtocolContext,
    stream_id: &str,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let event_id = builder.create_string(context.event_id().as_str());
    let stream_id = builder.create_string(stream_id);
    let producer_id = builder.create_string(&context.producer_id);
    let workspace_id = builder.create_string(&context.identity.workspace_id);
    let launch_id = context
        .identity
        .launch_id()
        .map(|value| builder.create_string(value.as_str()));
    let instance_id = context
        .identity
        .instance_id()
        .map(|value| builder.create_string(value.as_str()));
    EventMetadata::create(
        builder,
        &EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            sequence: context.sequence.get(),
            producer_id: Some(producer_id),
            producer_incarnation: context.producer_incarnation,
            workspace_id: Some(workspace_id),
            launch_id,
            instance_id,
            occurred_at_unix_nanos,
            published_at_unix_nanos: now_unix_nanos(),
            ..Default::default()
        },
    )
}

pub fn view_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &ViewProtocolContext,
    snapshot_id: &str,
    view_key: &str,
    as_of_unix_nanos: u64,
    applied_revision: Option<u64>,
) -> WIPOffset<ViewMetadata<'a>> {
    let snapshot_id = builder.create_string(snapshot_id);
    let resource_id = builder.create_string(&context.resource_id);
    let view_key = builder.create_string(view_key);
    let owner_id = builder.create_string(&context.owner_id);
    let workspace_id = builder.create_string(&context.identity.workspace_id);
    let launch_id = context
        .identity
        .launch_id()
        .map(|value| builder.create_string(value.as_str()));
    let instance_id = context
        .identity
        .instance_id()
        .map(|value| builder.create_string(value.as_str()));
    ViewMetadata::create(
        builder,
        &ViewMetadataArgs {
            snapshot_id: Some(snapshot_id),
            resource_id: Some(resource_id),
            resource_epoch: 1,
            view_key: Some(view_key),
            owner_id: Some(owner_id),
            workspace_id: Some(workspace_id),
            launch_id,
            instance_id,
            generation: context.generation.get(),
            as_of_unix_nanos,
            published_at_unix_nanos: now_unix_nanos(),
            completeness: ViewCompleteness::COMPLETE,
            applied_revision,
        },
    )
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}
