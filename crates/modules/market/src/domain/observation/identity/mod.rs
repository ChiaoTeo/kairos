mod capability;
mod error;
mod key;
mod kind;
mod qualifier;
mod scope;

pub(crate) use capability::validate_observation_selectors;
pub use error::ObservationIdentityError;
pub use key::MarketViewKey;
pub use kind::ObservationKind;
pub use qualifier::ObservationQualifier;
pub use scope::ObservationScope;

#[cfg(test)]
mod tests {
    use super::{ObservationIdentityError, ObservationQualifier, ObservationScope};

    #[test]
    fn identity_validation_errors_have_stable_codes_and_fields() {
        let network_error =
            ObservationScope::consolidated("instrument:equity:US:AAPL:common", Some(" ".into()))
                .unwrap_err();
        assert_eq!(network_error, ObservationIdentityError::EmptyNetworkId);
        assert_eq!(
            network_error.code(),
            "market.observation_identity.empty_network_id"
        );

        assert_eq!(
            ObservationQualifier::new("../bars"),
            Err(ObservationIdentityError::InvalidPathComponent { field: "qualifier" })
        );
    }
}
