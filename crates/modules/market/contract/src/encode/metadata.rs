use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::ProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::{EventMetadata, ViewMetadata};

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
    kairos_protocol::metadata::event_metadata(
        builder,
        context,
        "market.events",
        occurred_at_unix_nanos,
    )
}

pub fn view_metadata<'a, A: Allocator + 'a>(
    builder: &mut FlatBufferBuilder<'a, A>,
    context: &EncodeContext,
    key: &crate::MarketViewKey,
    as_of_unix_nanos: u64,
) -> WIPOffset<ViewMetadata<'a>> {
    let canonical_key = key.canonical_key();
    kairos_protocol::metadata::view_metadata(
        builder,
        context,
        &format!("{canonical_key}:{}", context.generation),
        &canonical_key,
        as_of_unix_nanos,
        None,
    )
}
