//! Stable provider-command delivery and reconciliation facts.

/// What the client can prove about delivery when a transport operation fails.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeliveryCertainty {
    /// The request failed locally, failed before write, or was explicitly
    /// refused before the provider could apply its intended side effect.
    NotSent,
    /// The provider may have received and applied the command, but no
    /// authoritative response was observed.
    MayHaveBeenSent,
    /// The provider acknowledged the command. The acknowledged payload is
    /// carried by `CommandOutcome::Confirmed`.
    Acknowledged,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderRejection {
    pub code: Option<String>,
    pub message: String,
    pub provider_request_id: Option<String>,
}

impl ProviderRejection {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            code: None,
            message: message.into(),
            provider_request_id: None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IndeterminateCommand {
    pub certainty: DeliveryCertainty,
    pub message: String,
    pub provider_request_id: Option<String>,
}

impl IndeterminateCommand {
    pub fn may_have_been_sent(message: impl Into<String>) -> Self {
        Self {
            certainty: DeliveryCertainty::MayHaveBeenSent,
            message: message.into(),
            provider_request_id: None,
        }
    }
}

/// A command result separates provider rejection from an ambiguous delivery.
/// Callers must reconcile `Indeterminate` on the same provider/account route;
/// it is not a retryable transport error.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CommandOutcome<T> {
    Confirmed(T),
    Rejected(ProviderRejection),
    Indeterminate(IndeterminateCommand),
}

impl<T> CommandOutcome<T> {
    pub fn map<U>(self, map: impl FnOnce(T) -> U) -> CommandOutcome<U> {
        match self {
            Self::Confirmed(value) => CommandOutcome::Confirmed(map(value)),
            Self::Rejected(rejection) => CommandOutcome::Rejected(rejection),
            Self::Indeterminate(command) => CommandOutcome::Indeterminate(command),
        }
    }
}
