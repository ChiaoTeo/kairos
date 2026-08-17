use std::error::Error;
use std::future::{ready, Future};

use crate::{ConfluxEvent, Context, Contract, RestResponseOf};

/// A closed Conflux process definition with one global event handler.
pub trait ConfluxActor: Send + Sized + 'static {
    type FatalError: Error + Send + Sync + 'static;
    type Contract: Contract;
    type LocalEvent: Send + 'static;

    fn started<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a {
        ready(Ok(()))
    }

    /// Handles every REST, Contract, Integration, or local event.
    ///
    /// REST events return `Some(response)`; events without a response return
    /// `None`. Conflux delivers the returned value to the Handle caller.
    fn handle<'a>(
        &'a mut self,
        event: ConfluxEvent<Self::Contract, Self::LocalEvent>,
        context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<Option<RestResponseOf<Self::Contract>>, Self::FatalError>>
           + Send
           + 'a;

    fn stopping<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a {
        ready(Ok(()))
    }
}
