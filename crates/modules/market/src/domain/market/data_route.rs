use std::collections::BTreeSet;

use kairos_primitives::market::{Provider, SubscriptionSymbol};
use kairos_primitives::reference::MarketId;
use serde::{Deserialize, Serialize};

use crate::domain::source::MarketFeedId;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub(crate) struct ProviderSegmentCode(String);

impl ProviderSegmentCode {
    pub(crate) fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into().trim().to_ascii_lowercase();
        if value.is_empty() || value.chars().any(char::is_whitespace) {
            return Err("provider segment must be a non-empty token without whitespace".into());
        }
        Ok(Self(value))
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
}

impl PartialEq<&str> for ProviderSegmentCode {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

impl std::ops::Deref for ProviderSegmentCode {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

/// A business fact that one provider can satisfy observations for a canonical Market.
///
/// Runtime feed identity, provider-native product and subscription symbol are
/// deliberately excluded. They belong to `AttachedMarketDataRoute`.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ResolvedMarketDataRoute {
    pub market_id: MarketId,
    pub provider: Provider,
    #[serde(default)]
    pub observation_kinds: BTreeSet<crate::domain::observation::ObservationKind>,
}

impl ResolvedMarketDataRoute {
    pub fn new(
        market_id: MarketId,
        provider: Provider,
        observation_kinds: impl IntoIterator<Item = crate::domain::observation::ObservationKind>,
    ) -> Self {
        Self {
            market_id,
            provider,
            observation_kinds: observation_kinds.into_iter().collect(),
        }
    }
}

/// Private runtime attachment used to turn one resolved business route into a
/// provider subscription. The feed identity is operational, not a Strategy
/// or Market contract identity.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct AttachedMarketDataRoute {
    pub route: ResolvedMarketDataRoute,
    pub feed_id: MarketFeedId,
    pub provider_segment: ProviderSegmentCode,
    pub subscription_symbol: SubscriptionSymbol,
}

/// Market-data access selected by composition for one canonical market.
///
/// All fields are required: application and source code consume this resolved
/// route and never infer provider facts from a listing symbol or market kind.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub(crate) struct ProviderRouteBinding {
    pub provider: Provider,
    pub provider_segment: ProviderSegmentCode,
    #[serde(alias = "provider_symbol")]
    pub subscription_symbol: SubscriptionSymbol,
    /// Code-owned adapter capabilities resolved by composition. These are
    /// independent from whether a workspace currently configured a source.
    #[serde(default)]
    pub observation_capabilities: BTreeSet<crate::domain::observation::ObservationKind>,
}

impl ProviderRouteBinding {
    pub(crate) fn new(
        provider: impl Into<String>,
        provider_segment: impl Into<String>,
        subscription_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        Ok(Self {
            provider: Provider::new(provider.into()).map_err(|error| error.to_string())?,
            provider_segment: ProviderSegmentCode::new(provider_segment.into())
                .map_err(|error| error.to_string())?,
            subscription_symbol: SubscriptionSymbol::new(subscription_symbol.into())
                .map_err(|error| error.to_string())?,
            observation_capabilities: BTreeSet::new(),
        })
    }

    pub(crate) fn with_observation_capabilities(
        mut self,
        capabilities: impl IntoIterator<Item = crate::domain::observation::ObservationKind>,
    ) -> Self {
        self.observation_capabilities = capabilities.into_iter().collect();
        self
    }
}
