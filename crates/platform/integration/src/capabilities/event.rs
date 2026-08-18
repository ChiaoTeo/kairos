//! Unified consumption for one physical provider stream carrying multiple
//! normalized event domains.

use std::future::Future;

use crate::{ExternalParticipantEvent, IntegrationError};

pub trait ParticipantEventStream: Send {
    fn next(
        &mut self,
    ) -> impl Future<Output = Result<ExternalParticipantEvent, IntegrationError>> + Send;
}
