//! Application result contract for provider commands.

pub use crate::domain::operation::{
    CommandOutcome, DeliveryCertainty, IndeterminateCommand, ProviderRejection,
};

/// `Err` is reserved for a failure proven to happen before a side effect could
/// occur, or for an authentication/authorization failure proving that the
/// command was not accepted. Ambiguous delivery is `CommandOutcome::Indeterminate`.
pub type CommandResult<T> = Result<CommandOutcome<T>, super::IntegrationError>;
