use kairos_primitives::SourceId;
use serde::{Deserialize, Serialize};

use super::qualifier::validate_path_component;
use super::{ObservationKind, ObservationQualifier};

/// Stable identity for one current market-data projection.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketViewKey {
    pub source_id: SourceId,
    pub scope_key: String,
    pub kind: ObservationKind,
    pub qualifier: Option<ObservationQualifier>,
}

impl MarketViewKey {
    pub fn new(
        source_id: impl AsRef<str>,
        scope_key: impl Into<String>,
        kind: ObservationKind,
    ) -> Result<Self, String> {
        let source_id = SourceId::new(source_id.as_ref()).map_err(|error| error.to_string())?;
        let scope_key = scope_key.into();
        validate_path_component("source_id", &source_id)?;
        validate_path_component("scope_key", &scope_key)?;
        validate_path_component("kind", kind.as_str())?;
        Ok(Self {
            source_id,
            scope_key,
            kind,
            qualifier: None,
        })
    }

    pub fn with_qualifier(
        source_id: impl AsRef<str>,
        scope_key: impl Into<String>,
        kind: ObservationKind,
        qualifier: impl Into<String>,
    ) -> Result<Self, String> {
        let mut value = Self::new(source_id, scope_key, kind)?;
        value.qualifier = Some(ObservationQualifier::new(qualifier)?);
        Ok(value)
    }

    pub fn as_str(&self) -> String {
        let base = format!(
            "market.view.{}.{}.{}",
            self.source_id,
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
            self.source_id.as_str(),
            self.scope_key.as_str(),
            self.kind.as_str(),
        ];
        if let Some(qualifier) = &self.qualifier {
            parts.push(qualifier.as_str());
        }
        parts
    }
}
