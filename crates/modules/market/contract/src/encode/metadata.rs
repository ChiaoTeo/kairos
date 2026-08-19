use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::ProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::{
    EventMetadata, EventMetadataArgs, ViewCompleteness, ViewMetadata, ViewMetadataArgs,
};

#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub common: ProtocolContext,
}

impl std::ops::Deref for EncodeContext {
    type Target = ProtocolContext;
    fn deref(&self) -> &Self::Target {
        &self.common
    }
}

impl EncodeContext {
    pub fn event(
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            common: ProtocolContext::event(producer_id, identity, sequence, event_id)?,
        })
    }

    pub fn view(
        producer_id: impl Into<String>,
        owner_id: impl Into<String>,
        identity: InstanceIdentity,
        generation: u64,
        resource_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            common: ProtocolContext::view(
                producer_id,
                owner_id,
                identity,
                generation,
                resource_id,
            )?,
        })
    }
}

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let event_id = builder.create_string(
        context
            .event_id
            .as_ref()
            .expect("event context carries event identity")
            .as_str(),
    );
    let stream_id = builder.create_string("market.events");
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
    context: &EncodeContext,
    key: &crate::MarketViewKey,
    as_of_unix_nanos: u64,
) -> WIPOffset<ViewMetadata<'a>> {
    let snapshot_id =
        builder.create_string(&format!("{}:{}", key.canonical_key(), context.generation));
    let resource_id = builder.create_string(&context.resource_id);
    let view_key = builder.create_string(&key.canonical_key());
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
            applied_revision: None,
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
