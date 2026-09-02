use flatbuffers::{Allocator, FlatBufferBuilder, WIPOffset};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::EventProtocolContext;
use kairos_protocol::generated::kairos::common::v_2::EventMetadata;

#[derive(Clone, Debug)]
pub struct EncodeContext {
    pub common: EventProtocolContext,
}

impl std::ops::Deref for EncodeContext {
    type Target = EventProtocolContext;
    fn deref(&self) -> &Self::Target {
        &self.common
    }
}

impl EncodeContext {
    pub fn event(
        producer_id: impl Into<String>,
        producer_incarnation: u64,
        identity: InstanceIdentity,
        sequence: u64,
        event_id: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            common: EventProtocolContext::new(
                producer_id,
                producer_incarnation,
                identity,
                sequence,
                event_id,
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
