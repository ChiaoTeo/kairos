use serde::{Deserialize, Serialize};

/// Stable identity for one current market-data projection.
///
/// The source is deliberately part of the identity: the same instrument may
/// be observed from several venues or provider connections, and quote/trade
/// projections must not overwrite one another. Qualifiers distinguish
/// standard variants such as bar timeframes.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct MarketViewKey {
    pub source_id: String,
    pub market_id: String,
    pub kind: String,
    pub qualifier: String,
}

impl MarketViewKey {
    pub fn new(
        source_id: impl Into<String>,
        market_id: impl Into<String>,
        kind: impl Into<String>,
    ) -> Result<Self, String> {
        let value = Self {
            source_id: source_id.into(),
            market_id: market_id.into(),
            kind: kind.into(),
            qualifier: String::new(),
        };
        for (name, field) in [
            ("source_id", &value.source_id),
            ("market_id", &value.market_id),
            ("kind", &value.kind),
        ] {
            if field.trim().is_empty() {
                return Err(format!("market view {name} is required"));
            }
        }
        Ok(value)
    }

    pub fn with_qualifier(
        source_id: impl Into<String>,
        market_id: impl Into<String>,
        kind: impl Into<String>,
        qualifier: impl Into<String>,
    ) -> Result<Self, String> {
        let mut value = Self::new(source_id, market_id, kind)?;
        value.qualifier = qualifier.into();
        if value.qualifier.trim().is_empty() {
            return Err("market view qualifier cannot be blank".into());
        }
        Ok(value)
    }

    pub fn as_str(&self) -> String {
        let base = format!(
            "market.view.{}.{}.{}",
            self.source_id, self.market_id, self.kind
        );
        if self.qualifier.is_empty() {
            base
        } else {
            format!("{base}.{}", self.qualifier)
        }
    }

    pub fn path_parts(&self) -> Vec<&str> {
        let mut parts = vec![
            self.source_id.as_str(),
            self.market_id.as_str(),
            self.kind.as_str(),
        ];
        if !self.qualifier.is_empty() {
            parts.push(self.qualifier.as_str());
        }
        parts
    }
}
