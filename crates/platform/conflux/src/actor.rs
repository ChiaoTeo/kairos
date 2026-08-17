use std::error::Error;
use std::future::{ready, Future};

use crate::{CommitContext, ConfluxSystem, Context, RestCallOf, ServedContract};

/// A closed Conflux process definition.
///
/// `Contract` is the process's one outward service and is inseparable from the
/// Actor implementation. The system `S`, rather than the Actor, owns the
/// complete client and connection universe. `Ingress` and `Output` are
/// ordinary closed Rust enums selected by the module.
pub trait ConfluxActor<S>: Send + Sized + 'static
where
    S: ConfluxSystem,
{
    type FatalError: Error + Send + Sync + 'static;
    type Contract: ServedContract;
    type Ingress: From<RestCallOf<Self::Contract>> + Send + 'static;
    type Output: Send + 'static;

    fn started<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self, S>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a {
        ready(Ok(()))
    }

    fn handle<'a>(
        &'a mut self,
        ingress: Self::Ingress,
        context: &'a mut Context<'_, Self, S>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a;

    fn commit<'a>(
        &'a mut self,
        output: Self::Output,
        context: &'a mut CommitContext<'_, Self, S>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a;

    fn stopping<'a>(
        &'a mut self,
        _context: &'a mut Context<'_, Self, S>,
    ) -> impl Future<Output = Result<(), Self::FatalError>> + Send + 'a {
        ready(Ok(()))
    }
}
