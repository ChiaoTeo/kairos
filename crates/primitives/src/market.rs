use serde::{Deserialize, Serialize};

use crate::DomainTypeError;
use crate::text::text_type;

text_type!(SubscriptionId);
text_type!(SubscriptionSymbol);

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct SourceId(String);

impl SourceId {
    pub fn new(value: impl Into<String>) -> Result<Self, DomainTypeError> {
        let value = value.into().trim().to_ascii_lowercase();
        if value.is_empty() {
            return Err(DomainTypeError::Empty {
                type_name: "SourceId",
            });
        }
        if value.chars().any(char::is_whitespace) {
            return Err(DomainTypeError::Invalid {
                type_name: "SourceId",
                reason: "whitespace is not allowed",
            });
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn into_inner(self) -> String {
        self.0
    }
}

impl<'de> Deserialize<'de> for SourceId {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(serde::de::Error::custom)
    }
}

impl AsRef<str> for SourceId {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl std::ops::Deref for SourceId {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        self.as_str()
    }
}

impl std::fmt::Display for SourceId {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl TryFrom<String> for SourceId {
    type Error = DomainTypeError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for SourceId {
    type Error = DomainTypeError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}
