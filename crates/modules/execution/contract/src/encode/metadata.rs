use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_protocol::generated::kairos::common::v_2::{
    EventMetadata, EventMetadataArgs, ViewCompleteness, ViewMetadata, ViewMetadataArgs,
};
use kairos_protocol::InstanceIdentity;
#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub producer_id: String,
    pub owner_id: String,
    pub identity: InstanceIdentity,
    pub sequence: u64,
    pub event_id: String,
    pub generation: u64,
    pub resource_id: String,
}
impl EncodeContext {
    pub fn event(
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Self {
        Self {
            producer_id: producer_id.into(),
            owner_id: String::new(),
            identity,
            sequence,
            event_id: event_id.into(),
            generation: 0,
            resource_id: String::new(),
        }
    }
    pub fn view(
        producer_id: impl Into<String>,
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        generation: u64,
        resource_id: impl Into<String>,
    ) -> Self {
        Self {
            producer_id: producer_id.into(),
            owner_id: owner_id.into(),
            identity,
            sequence: 0,
            event_id: String::new(),
            generation,
            resource_id: resource_id.into(),
        }
    }
}
pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let event_id = builder.create_string(&context.event_id);
    let stream_id = builder.create_string("execution.events");
    let producer_id = builder.create_string(&context.producer_id);
    let workspace_id = builder.create_string(&context.identity.workspace_id);
    let launch_id = non_empty(builder, &context.identity.launch_id);
    let instance_id = non_empty(builder, &context.identity.instance_id);
    EventMetadata::create(
        builder,
        &EventMetadataArgs {
            event_id: Some(event_id),
            stream_id: Some(stream_id),
            sequence: context.sequence,
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
    context: &EncodeContext,
    key: &crate::ExecutionViewKey,
    as_of_unix_nanos: u64,
    applied_revision: u64,
) -> WIPOffset<ViewMetadata<'a>> {
    let snapshot_id =
        builder.create_string(&format!("{}:{}", key.canonical_key(), context.generation));
    let resource_id = builder.create_string(&context.resource_id);
    let view_key = builder.create_string(&key.canonical_key());
    let owner_id = builder.create_string(&context.owner_id);
    let workspace_id = builder.create_string(&context.identity.workspace_id);
    let launch_id = non_empty(builder, &context.identity.launch_id);
    let instance_id = non_empty(builder, &context.identity.instance_id);
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
            generation: context.generation,
            as_of_unix_nanos,
            published_at_unix_nanos: now_unix_nanos(),
            completeness: ViewCompleteness::COMPLETE,
            applied_revision: Some(applied_revision),
        },
    )
}
fn non_empty<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}
fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64
}
