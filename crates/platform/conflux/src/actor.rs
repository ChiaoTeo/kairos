use std::error::Error;
use std::future::{Future, ready};

use crate::{ConfluxEvent, Context};

/// A closed Conflux process definition with one global event handler.
pub trait ConfluxActor: Send + 'static + Sized {
    type FatalError: Error + Send + Sync + 'static;
    type LocalEvent: Send + 'static;

    fn started<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + 'a {
        ready(Ok(()))
    }

    /// Handles every Contract, Integration, system, timer, or local event.
    fn handle<'a>(
        &'a mut self,
        event: ConfluxEvent<Self::LocalEvent>,
        context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + 'a;

    fn stopping<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + 'a {
        ready(Ok(()))
    }
}
