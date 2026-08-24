use kairos_primitives::market::Provider;
use serde::{Deserialize, Serialize};

use super::qualifier::validate_path_component;
use super::{ObservationKind, ObservationQualifier};

/// Stable identity for one current market-data view.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketViewKey {
    pub provider: Provider,
    pub scope_key: String,
    pub kind: ObservationKind,
    pub qualifier: Option<ObservationQualifier>,
}

impl MarketViewKey {
    pub fn new(
        provider: impl AsRef<str>,
        scope_key: impl Into<String>,
        kind: ObservationKind,
    ) -> Result<Self, String> {
        let provider = Provider::new(provider.as_ref()).map_err(|error| error.to_string())?;
        let scope_key = scope_key.into();
        validate_path_component("provider", &provider)?;
        validate_path_component("scope_key", &scope_key)?;
        validate_path_component("kind", kind.as_str())?;
        Ok(Self {
            provider,
            scope_key,
            kind,
            qualifier: None,
        })
    }

    pub fn with_qualifier(
        provider: impl AsRef<str>,
        scope_key: impl Into<String>,
        kind: ObservationKind,
        qualifier: impl Into<String>,
    ) -> Result<Self, String> {
        let mut value = Self::new(provider, scope_key, kind)?;
        value.qualifier = Some(ObservationQualifier::new(qualifier)?);
        Ok(value)
    }

    pub fn as_str(&self) -> String {
        let base = format!(
            "market.view.{}.{}.{}",
            self.provider,
            self.scope_key,
            self.kind.as_str()
        );
        match &self.qualifier {
            Some(qualifier) => format!("{base}.{}", qualifier.as_str()),
            None => base,
        }
    }

    pub fn path_parts(&self) -> Vec<&str> {
        let mut parts = vec![
            self.provider.as_str(),
            self.scope_key.as_str(),
            self.kind.as_str(),
        ];
        if let Some(qualifier) = &self.qualifier {
            parts.push(qualifier.as_str());
        }
        parts
    }
}
