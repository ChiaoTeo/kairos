use std::path::{Path, PathBuf};

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum MarketViewKind {
    Quote,
    BarWindow,
    Greeks,
    Rate,
    Ticker24h,
    MarkPrice,
    FundingRate,
    OpenInterest,
    IndexPrice,
    OrderBook,
    Freshness,
}

impl MarketViewKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Quote => "quote",
            Self::BarWindow => "bar",
            Self::Greeks => "greeks",
            Self::Rate => "rate",
            Self::Ticker24h => "ticker-24h",
            Self::MarkPrice => "mark-price",
            Self::FundingRate => "funding-rate",
            Self::OpenInterest => "open-interest",
            Self::IndexPrice => "index-price",
            Self::OrderBook => "order-book",
            Self::Freshness => "freshness",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MarketViewKey {
    pub scope_key: String,
    pub source_id: String,
    pub kind: MarketViewKind,
    pub qualifier: Option<String>,
}

impl MarketViewKey {
    pub fn new(
        scope_key: impl Into<String>,
        source_id: impl Into<String>,
        kind: MarketViewKind,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Self> {
        let scope_key = scope_key.into();
        let source_id = source_id.into();
        let qualifier = qualifier.map(Into::into);
        if scope_key.trim().is_empty() || source_id.trim().is_empty() {
            return Err(ContractError::Invalid("view identity is incomplete".into()));
        }
        Ok(Self {
            scope_key,
            source_id,
            kind,
            qualifier,
        })
    }

    pub fn canonical_key(&self) -> String {
        format!(
            "scope={};source={};view={};qualifier={}",
            self.scope_key,
            self.source_id,
            self.kind.as_str(),
            self.qualifier.as_deref().unwrap_or("")
        )
    }

    pub fn resource_path(&self, root: impl AsRef<Path>) -> PathBuf {
        root.as_ref()
            .join(format!("{}.e1.mmap", self.resource_id()))
    }

    pub fn resource_id(&self) -> String {
        let qualifier = self.qualifier.as_deref().unwrap_or("none");
        format!(
            "scope-{}-{}-{}-{}",
            component(&self.scope_key),
            component(&self.source_id),
            self.kind.as_str(),
            component(qualifier)
        )
    }
}

fn component(value: &str) -> String {
    value
        .as_bytes()
        .iter()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.') {
                format!("{}", *byte as char)
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}
