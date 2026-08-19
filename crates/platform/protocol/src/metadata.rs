//! Common FlatBuffers metadata builders.

use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};

use crate::context::ProtocolContext;
use crate::generated::kairos::common::v_2::{
    EventMetadata, EventMetadataArgs, ViewCompleteness, ViewMetadata, ViewMetadataArgs,
};

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &ProtocolContext,
    stream_id: &str,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let event_id = builder.create_string(
        context
            .event_id
            .as_ref()
            .expect("event context carries event identity")
            .as_str(),
    );
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
    context: &ProtocolContext,
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
