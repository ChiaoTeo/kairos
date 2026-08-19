//! Synchronous participant-event consumption for dedicated blocking workers.

use crate::{ExternalParticipantEvent, IntegrationError};

pub trait ParticipantEventStream: Send {
    fn next(&mut self) -> Result<ExternalParticipantEvent, IntegrationError>;
}
