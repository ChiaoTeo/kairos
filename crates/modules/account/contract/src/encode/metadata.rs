use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::{ActorId, InstanceIdentity};
use kairos_primitives::time::Sequence;
use kairos_protocol::ProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::{EventMetadata, ViewMetadata};

#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub common: ProtocolContext,
    pub account_runtime_id: ActorId,
    pub applied_revision: Option<Sequence>,
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
        account_runtime_id: impl Into<String>,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            common: ProtocolContext::event(producer_id, identity, sequence, event_id)?,
            account_runtime_id: ActorId::new(account_runtime_id)?,
            applied_revision: None,
        })
    }

    pub fn view(
        producer_id: impl Into<String>,
        owner_id: impl Into<String>,
        account_runtime_id: impl Into<String>,
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
            account_runtime_id: ActorId::new(account_runtime_id)?,
            applied_revision: None,
        })
    }

    pub fn with_applied_revision(mut self, applied_revision: u64) -> Self {
        self.applied_revision = Some(applied_revision.into());
        self
    }
}

pub fn event_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    occurred_at_unix_nanos: u64,
) -> WIPOffset<EventMetadata<'a>> {
    let stream_id = format!("account.events/{}", context.account_runtime_id);
    kairos_protocol::metadata::event_metadata(builder, context, &stream_id, occurred_at_unix_nanos)
}

pub fn view_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    key: &crate::AccountViewKey,
    as_of_unix_nanos: u64,
) -> WIPOffset<ViewMetadata<'a>> {
    let canonical_key = key.canonical_key();
    kairos_protocol::metadata::view_metadata(
        builder,
        context,
        &format!("{canonical_key}:{}", context.generation),
        &canonical_key,
        as_of_unix_nanos,
        context.applied_revision.map(Sequence::get),
    )
}
