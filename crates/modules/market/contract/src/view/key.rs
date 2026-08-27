use kairos_primitives::market::Provider;

use crate::{ContractError, ContractResult};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum MarketViewKind {
    Quote,
    Bar,
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
            Self::Bar => "bar",
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
    pub provider: Provider,
    pub kind: MarketViewKind,
    pub qualifier: Option<String>,
}

impl MarketViewKey {
    pub fn new(
        scope_key: impl Into<String>,
        provider: impl AsRef<str>,
        kind: MarketViewKind,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Self> {
        let scope_key = scope_key.into();
        let provider = Provider::new(provider.as_ref())
            .map_err(|error| ContractError::Invalid(error.to_string()))?;
        let qualifier = qualifier.map(Into::into);
        if scope_key.trim().is_empty() {
            return Err(ContractError::Invalid("view identity is incomplete".into()));
        }
        Ok(Self {
            scope_key,
            provider,
            kind,
            qualifier,
        })
    }

    pub fn canonical_key(&self) -> String {
        format!(
            "scope={};provider={};view={};qualifier={}",
            self.scope_key,
            self.provider,
            self.kind.as_str(),
            self.qualifier.as_deref().unwrap_or("")
        )
    }
}
