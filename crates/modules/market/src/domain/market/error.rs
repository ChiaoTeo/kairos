use kairos_primitives::DomainTypeError;

use crate::domain::observation::{ObservationIdentityError, ObservationKind};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ResolvedMarketError {
    InvalidSemantic {
        field: &'static str,
        source: DomainTypeError,
    },
    InvalidObservationScope(ObservationIdentityError),
    UnknownInstrumentKind,
    UnknownStatus,
    DifferentMarketIdentity {
        current: String,
        incoming: String,
    },
    MissingRuntimeBinding,
    UnsupportedObservation {
        observation_kind: ObservationKind,
    },
    NoSubscribableObservations,
}

impl ResolvedMarketError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidSemantic { .. } => "market.resolution.invalid_semantic",
            Self::InvalidObservationScope(_) => "market.resolution.invalid_observation_scope",
            Self::UnknownInstrumentKind => "market.resolution.unknown_instrument_kind",
            Self::UnknownStatus => "market.resolution.unknown_status",
            Self::DifferentMarketIdentity { .. } => "market.resolution.different_market_identity",
            Self::MissingRuntimeBinding => "market.resolution.missing_runtime_binding",
            Self::UnsupportedObservation { .. } => "market.resolution.unsupported_observation",
            Self::NoSubscribableObservations => "market.resolution.no_subscribable_observations",
        }
    }
}

impl std::fmt::Display for ResolvedMarketError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidSemantic { field, source } => {
                write!(formatter, "invalid resolved Market {field}: {source}")
            },
            Self::InvalidObservationScope(error) => {
                write!(formatter, "invalid resolved Market scope: {error}")
            },
            Self::UnknownInstrumentKind => {
                formatter.write_str("market instrument kind must be known")
            },
            Self::UnknownStatus => formatter.write_str("market status must be known"),
            Self::DifferentMarketIdentity { current, incoming } => write!(
                formatter,
                "cannot merge provider routes for different Markets: {current} and {incoming}"
            ),
            Self::MissingRuntimeBinding => {
                formatter.write_str("resolved Market has no runtime provider binding")
            },
            Self::UnsupportedObservation { observation_kind } => {
                write!(
                    formatter,
                    "provider route does not support {observation_kind:?}"
                )
            },
            Self::NoSubscribableObservations => {
                formatter.write_str("provider route exposes no subscribable observations")
            },
        }
    }
}

impl std::error::Error for ResolvedMarketError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::InvalidSemantic { source, .. } => Some(source),
            Self::InvalidObservationScope(source) => Some(source),
            _ => None,
        }
    }
}

impl From<ObservationIdentityError> for ResolvedMarketError {
    fn from(error: ObservationIdentityError) -> Self {
        Self::InvalidObservationScope(error)
    }
}
