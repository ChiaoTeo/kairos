use serde::{Deserialize, Serialize};

use super::ObservationIdentityError;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ObservationQualifier(String);

impl ObservationQualifier {
    pub fn new(value: impl Into<String>) -> Result<Self, ObservationIdentityError> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(ObservationIdentityError::BlankQualifier);
        }
        validate_path_component("qualifier", &value)?;
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

pub(super) fn validate_path_component(
    name: &'static str,
    value: &str,
) -> Result<(), ObservationIdentityError> {
    if value.trim().is_empty() {
        return Err(ObservationIdentityError::MissingPathComponent { field: name });
    }
    if value.contains('/') || value.contains('\\') || value == "." || value == ".." {
        return Err(ObservationIdentityError::InvalidPathComponent { field: name });
    }
    Ok(())
}
