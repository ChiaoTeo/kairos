use kairos_primitives::reference::InstrumentKind;

use super::ObservationKind;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ObservationIdentityError {
    InvalidSemantic(kairos_primitives::DomainTypeError),
    EmptyNetworkId,
    BlankQualifier,
    MissingPathComponent {
        field: &'static str,
    },
    InvalidPathComponent {
        field: &'static str,
    },
    UnsupportedObservation {
        instrument_kind: InstrumentKind,
        observation_kind: ObservationKind,
    },
}

impl std::fmt::Display for ObservationIdentityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidSemantic(error) => {
                write!(formatter, "invalid observation identity: {error}")
            },
            Self::EmptyNetworkId => {
                formatter.write_str("observation network_id must be non-empty when present")
            },
            Self::BlankQualifier => formatter.write_str("market view qualifier cannot be blank"),
            Self::MissingPathComponent { field } => {
                write!(formatter, "market view {field} is required")
            },
            Self::InvalidPathComponent { field } => write!(
                formatter,
                "market view {field} contains an invalid path component"
            ),
            Self::UnsupportedObservation {
                instrument_kind,
                observation_kind,
            } => write!(
                formatter,
                "observation selector {observation_kind} is not supported by {instrument_kind} market"
            ),
        }
    }
}

impl std::error::Error for ObservationIdentityError {}

impl From<kairos_primitives::DomainTypeError> for ObservationIdentityError {
    fn from(error: kairos_primitives::DomainTypeError) -> Self {
        Self::InvalidSemantic(error)
    }
}

impl ObservationIdentityError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidSemantic(_) => "market.observation_identity.invalid_semantic",
            Self::EmptyNetworkId => "market.observation_identity.empty_network_id",
            Self::BlankQualifier => "market.observation_identity.blank_qualifier",
            Self::MissingPathComponent { .. } => {
                "market.observation_identity.missing_path_component"
            },
            Self::InvalidPathComponent { .. } => {
                "market.observation_identity.invalid_path_component"
            },
            Self::UnsupportedObservation { .. } => {
                "market.observation_identity.unsupported_observation"
            },
        }
    }
}
