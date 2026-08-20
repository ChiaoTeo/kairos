use kairos_conflux::ParticipantInstrumentTypeRef;
use kairos_primitives::account::{AccountId, SegmentKey};

#[derive(Clone)]
pub(crate) struct ExecutionConnectionPlan {
    pub(crate) route_id: String,
    pub(crate) required: bool,
    pub(crate) account_id: AccountId,
    pub(crate) segment_key: SegmentKey,
    pub(crate) instrument_type: ParticipantInstrumentTypeRef,
    pub(crate) entry_key: String,
    pub(crate) query_key: String,
    pub(crate) stream_key: String,
}
