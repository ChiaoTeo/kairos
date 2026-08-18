//! Unified consumption for one physical provider stream carrying multiple
//! normalized event domains.

use std::task::{Context, Poll};

use crate::{ExternalParticipantEvent, IntegrationError};

pub trait ParticipantEventStream: Send {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalParticipantEvent, IntegrationError>>;
}
