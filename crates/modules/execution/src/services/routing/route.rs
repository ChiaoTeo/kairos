//! Execution-owned route identity and command matching.

use super::*;

/// One business route bound to one concrete Integration capability.
pub(crate) struct ExecutionRoute<C> {
    pub(crate) route_id: String,
    pub(crate) account_id: AccountId,
    pub(crate) segment_key: SegmentKey,
    /// Exact participant-owned product discriminator for this route. It is
    /// deliberately opaque to Execution: composition maps provider-native
    /// types into this value and routing only compares identity.
    pub(crate) provider_instrument_type: Option<ParticipantInstrumentTypeRef>,
    pub(crate) descriptor: ConnectionDescriptor,
    pub(crate) connection: C,
}

impl<C> ExecutionRoute<C> {
    pub(crate) fn new(
        route_id: impl Into<String>,
        account_id: AccountId,
        segment_key: SegmentKey,
        provider_instrument_type: Option<ParticipantInstrumentTypeRef>,
        descriptor: ConnectionDescriptor,
        connection: C,
    ) -> Result<Self, String> {
        let route_id = route_id.into();
        if route_id.trim().is_empty() {
            return Err("execution route_id is required".into());
        }
        descriptor.validate()?;
        Ok(Self {
            route_id,
            account_id,
            segment_key,
            provider_instrument_type,
            descriptor,
            connection,
        })
    }

    pub(super) fn matches_order(&self, request: &OrderEntryRequest) -> bool {
        self.account_id == request.account_id
            && self.segment_key == request.segment_key
            && self.descriptor.participant == request.provider_instrument.participant
            && self.provider_instrument_type == request.provider_instrument.instrument_type
    }
}
