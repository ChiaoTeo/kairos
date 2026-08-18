//! Synchronous consumption for multiplexed provider streams.

use std::time::Duration;

use crate::{ExternalParticipantEvent, IntegrationError};

pub trait ParticipantEventStream: Send {
    fn next(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<ExternalParticipantEvent>, IntegrationError>;
}
