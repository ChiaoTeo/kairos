use kairos_primitives::MarketId;
use serde::{Deserialize, Serialize};

use super::qualifier::validate_path_component;
use super::{ObservationKind, ObservationQualifier};

/// Stable identity for one current market-data projection.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketViewKey {
    pub source_id: String,
    pub market_id: MarketId,
    pub kind: ObservationKind,
    pub qualifier: Option<ObservationQualifier>,
}

impl MarketViewKey {
    pub fn new(
        source_id: impl Into<String>,
        market_id: impl Into<String>,
        kind: ObservationKind,
    ) -> Result<Self, String> {
        let source_id = source_id.into();
        let market_id = MarketId::new(market_id.into()).map_err(|error| error.to_string())?;
        validate_path_component("source_id", &source_id)?;
        validate_path_component("market_id", market_id.as_str())?;
        validate_path_component("kind", kind.as_str())?;
        Ok(Self {
            source_id,
            market_id,
            kind,
            qualifier: None,
        })
    }

    pub fn with_qualifier(
        source_id: impl Into<String>,
        market_id: impl Into<String>,
        kind: ObservationKind,
        qualifier: impl Into<String>,
    ) -> Result<Self, String> {
        let mut value = Self::new(source_id, market_id, kind)?;
        value.qualifier = Some(ObservationQualifier::new(qualifier)?);
        Ok(value)
    }

    pub fn as_str(&self) -> String {
        let base = format!(
            "market.view.{}.{}.{}",
            self.source_id,
            self.market_id,
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
            self.market_id.as_str(),
            self.kind.as_str(),
        ];
        if let Some(qualifier) = &self.qualifier {
            parts.push(qualifier.as_str());
        }
        parts
    }
}
