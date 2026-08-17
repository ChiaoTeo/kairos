use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ObservationQualifier(String);

impl ObservationQualifier {
    pub fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err("market view qualifier cannot be blank".into());
        }
        validate_path_component("qualifier", &value)?;
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

pub(super) fn validate_path_component(name: &str, value: &str) -> Result<(), String> {
    if value.trim().is_empty() {
        return Err(format!("market view {name} is required"));
    }
    if value.contains('/') || value.contains('\\') || value == "." || value == ".." {
        return Err(format!(
            "market view {name} contains an invalid path component"
        ));
    }
    Ok(())
}
