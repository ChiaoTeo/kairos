//! Closed Reference adapter bindings.
//!
//! These variants describe code-owned Reference source adapters. They are not
//! a shared market segment vocabulary and never cross into the Reference
//! domain. Runtime state persists the resulting Reference source definition;
//! the binding itself remains a private adapter-selection detail.

use kairos_primitives::market::Provider;
use kairos_primitives::reference::ReferenceSourceId;

use super::{HyperliquidProduct, OkxProduct};
use crate::domain::{
    ReferenceError, ReferenceResult, ReferenceSourceDefinition, SourceConnectionId,
    SourceDesiredState, SourceScope, SourceSyncPolicy,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ReferenceSourceBinding {
    Binance(BinanceReferenceSource),
    Okx(OkxProduct),
    Hyperliquid(HyperliquidProduct),
    Massive(MassiveReferenceSource),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BinanceReferenceSource {
    Spot,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MassiveReferenceSource {
    Equity,
    Options,
}

impl ReferenceSourceBinding {
    #[cfg(test)]
    pub(crate) const ALL: [Self; 14] = [
        Self::Binance(BinanceReferenceSource::Spot),
        Self::Binance(BinanceReferenceSource::UsdMFutures),
        Self::Binance(BinanceReferenceSource::CoinMFutures),
        Self::Binance(BinanceReferenceSource::Options),
        Self::Binance(BinanceReferenceSource::Equity),
        Self::Okx(OkxProduct::Spot),
        Self::Okx(OkxProduct::Margin),
        Self::Okx(OkxProduct::Swap),
        Self::Okx(OkxProduct::Futures),
        Self::Okx(OkxProduct::Option),
        Self::Hyperliquid(HyperliquidProduct::Spot),
        Self::Hyperliquid(HyperliquidProduct::Perpetual),
        Self::Massive(MassiveReferenceSource::Equity),
        Self::Massive(MassiveReferenceSource::Options),
    ];

    pub(crate) const fn source_id(self) -> &'static str {
        match self {
            Self::Binance(BinanceReferenceSource::Spot) => "binance-spot",
            Self::Binance(BinanceReferenceSource::UsdMFutures) => "binance-usdm-futures",
            Self::Binance(BinanceReferenceSource::CoinMFutures) => "binance-coinm-futures",
            Self::Binance(BinanceReferenceSource::Options) => "binance-options",
            Self::Binance(BinanceReferenceSource::Equity) => "binance-equity",
            Self::Okx(product) => product.source_id(),
            Self::Hyperliquid(product) => product.source_id(),
            Self::Massive(MassiveReferenceSource::Equity) => "massive-equity",
            Self::Massive(MassiveReferenceSource::Options) => "massive-options",
        }
    }

    pub(crate) const fn provider(self) -> &'static str {
        match self {
            Self::Binance(_) => "binance",
            Self::Okx(_) => "okx",
            Self::Hyperliquid(_) => "hyperliquid",
            Self::Massive(_) => "massive",
        }
    }

    pub(crate) const fn product(self) -> &'static str {
        match self {
            Self::Binance(BinanceReferenceSource::Spot) => "spot",
            Self::Binance(BinanceReferenceSource::UsdMFutures) => "usd-m-futures",
            Self::Binance(BinanceReferenceSource::CoinMFutures) => "coin-m-futures",
            Self::Binance(BinanceReferenceSource::Options) => "options",
            Self::Binance(BinanceReferenceSource::Equity) => "equity",
            Self::Okx(product) => product.profile_product(),
            Self::Hyperliquid(HyperliquidProduct::Spot) => "spot",
            Self::Hyperliquid(HyperliquidProduct::Perpetual) => "perpetual",
            Self::Massive(MassiveReferenceSource::Equity) => "equity",
            Self::Massive(MassiveReferenceSource::Options) => "options",
        }
    }

    pub(crate) const fn sync_policy(self) -> SourceSyncPolicy {
        match self {
            Self::Massive(MassiveReferenceSource::Equity) => SourceSyncPolicy::PagedSnapshot,
            Self::Massive(MassiveReferenceSource::Options) => SourceSyncPolicy::ScopedSnapshot,
            _ => SourceSyncPolicy::FullSnapshot,
        }
    }

    /// Business facts this adapter can establish for its declared scope.
    /// This is capability metadata, so an empty but complete provider catalog
    /// still proves absence inside that provider-defined scope.
    pub(crate) fn fact_kinds(
        self,
    ) -> std::collections::BTreeSet<kairos_reference_contract::ReferenceFactKind> {
        use kairos_reference_contract::ReferenceFactKind as Fact;

        let mut facts = [
            Fact::Venue,
            Fact::Asset,
            Fact::Instrument,
            Fact::Listing,
            Fact::Market,
            Fact::ProviderCatalogMembership,
            Fact::TradingRules,
        ]
        .into_iter()
        .collect::<std::collections::BTreeSet<_>>();
        if self == Self::Massive(MassiveReferenceSource::Equity) {
            facts.insert(Fact::VenueIdentifierMapping);
        }
        facts
    }

    pub(crate) const fn requires_credential(self) -> bool {
        matches!(
            self,
            Self::Binance(BinanceReferenceSource::Equity) | Self::Massive(_)
        )
    }

    pub(crate) fn from_source_id(source_id: &str) -> Option<Self> {
        match source_id {
            "binance-spot" => Some(Self::Binance(BinanceReferenceSource::Spot)),
            "binance-usdm-futures" => Some(Self::Binance(BinanceReferenceSource::UsdMFutures)),
            "binance-coinm-futures" => Some(Self::Binance(BinanceReferenceSource::CoinMFutures)),
            "binance-options" => Some(Self::Binance(BinanceReferenceSource::Options)),
            "binance-equity" | "binance-stocks" | "reference-binance-stocks" => {
                Some(Self::Binance(BinanceReferenceSource::Equity))
            },
            "okx-spot" => Some(Self::Okx(OkxProduct::Spot)),
            "okx-margin" => Some(Self::Okx(OkxProduct::Margin)),
            "okx-swap" => Some(Self::Okx(OkxProduct::Swap)),
            "okx-futures" => Some(Self::Okx(OkxProduct::Futures)),
            "okx-options" => Some(Self::Okx(OkxProduct::Option)),
            "hyperliquid-spot" => Some(Self::Hyperliquid(HyperliquidProduct::Spot)),
            "hyperliquid-perpetual" => Some(Self::Hyperliquid(HyperliquidProduct::Perpetual)),
            "massive-equity" => Some(Self::Massive(MassiveReferenceSource::Equity)),
            "massive-options" => Some(Self::Massive(MassiveReferenceSource::Options)),
            _ => None,
        }
    }

    pub(crate) const fn from_contract(
        binding: kairos_reference_contract::ReferenceSourceBinding,
    ) -> Self {
        use kairos_reference_contract::{
            BinanceReferenceSource as Binance, HyperliquidReferenceSource as Hyperliquid,
            MassiveReferenceSource as Massive, OkxReferenceSource as Okx,
            ReferenceSourceBinding as Contract,
        };
        match binding {
            Contract::Binance(Binance::Spot) => Self::Binance(BinanceReferenceSource::Spot),
            Contract::Binance(Binance::UsdMFutures) => {
                Self::Binance(BinanceReferenceSource::UsdMFutures)
            },
            Contract::Binance(Binance::CoinMFutures) => {
                Self::Binance(BinanceReferenceSource::CoinMFutures)
            },
            Contract::Binance(Binance::Options) => Self::Binance(BinanceReferenceSource::Options),
            Contract::Binance(Binance::Equity) => Self::Binance(BinanceReferenceSource::Equity),
            Contract::Okx(Okx::Spot) => Self::Okx(OkxProduct::Spot),
            Contract::Okx(Okx::Margin) => Self::Okx(OkxProduct::Margin),
            Contract::Okx(Okx::Swap) => Self::Okx(OkxProduct::Swap),
            Contract::Okx(Okx::Futures) => Self::Okx(OkxProduct::Futures),
            Contract::Okx(Okx::Options) => Self::Okx(OkxProduct::Option),
            Contract::Hyperliquid(Hyperliquid::Spot) => Self::Hyperliquid(HyperliquidProduct::Spot),
            Contract::Hyperliquid(Hyperliquid::Perpetual) => {
                Self::Hyperliquid(HyperliquidProduct::Perpetual)
            },
            Contract::Massive(Massive::Equity) => Self::Massive(MassiveReferenceSource::Equity),
            Contract::Massive(Massive::Options) => Self::Massive(MassiveReferenceSource::Options),
        }
    }

    pub(crate) const fn to_contract(self) -> kairos_reference_contract::ReferenceSourceBinding {
        use kairos_reference_contract::{
            BinanceReferenceSource as Binance, HyperliquidReferenceSource as Hyperliquid,
            MassiveReferenceSource as Massive, OkxReferenceSource as Okx,
            ReferenceSourceBinding as Contract,
        };
        match self {
            Self::Binance(BinanceReferenceSource::Spot) => Contract::Binance(Binance::Spot),
            Self::Binance(BinanceReferenceSource::UsdMFutures) => {
                Contract::Binance(Binance::UsdMFutures)
            },
            Self::Binance(BinanceReferenceSource::CoinMFutures) => {
                Contract::Binance(Binance::CoinMFutures)
            },
            Self::Binance(BinanceReferenceSource::Options) => Contract::Binance(Binance::Options),
            Self::Binance(BinanceReferenceSource::Equity) => Contract::Binance(Binance::Equity),
            Self::Okx(OkxProduct::Spot) => Contract::Okx(Okx::Spot),
            Self::Okx(OkxProduct::Margin) => Contract::Okx(Okx::Margin),
            Self::Okx(OkxProduct::Swap) => Contract::Okx(Okx::Swap),
            Self::Okx(OkxProduct::Futures) => Contract::Okx(Okx::Futures),
            Self::Okx(OkxProduct::Option) => Contract::Okx(Okx::Options),
            Self::Hyperliquid(HyperliquidProduct::Spot) => Contract::Hyperliquid(Hyperliquid::Spot),
            Self::Hyperliquid(HyperliquidProduct::Perpetual) => {
                Contract::Hyperliquid(Hyperliquid::Perpetual)
            },
            Self::Massive(MassiveReferenceSource::Equity) => Contract::Massive(Massive::Equity),
            Self::Massive(MassiveReferenceSource::Options) => Contract::Massive(Massive::Options),
        }
    }

    pub(crate) fn definition(
        self,
        scope: SourceScope,
        desired_state: SourceDesiredState,
        connection_id: Option<SourceConnectionId>,
    ) -> ReferenceResult<ReferenceSourceDefinition> {
        if self == Self::Massive(MassiveReferenceSource::Options) {
            if !matches!(
                scope.kind,
                crate::domain::SourceScopeKind::Global
                    | crate::domain::SourceScopeKind::Coverage
                    | crate::domain::SourceScopeKind::UnderlyingInstrument
            ) {
                return Err(ReferenceError::Invalid(
                    "Massive options Reference source requires global, coverage, or underlying-instrument scope"
                        .into(),
                ));
            }
        } else if scope != SourceScope::global() {
            return Err(ReferenceError::Invalid(format!(
                "{} Reference source only supports global scope",
                self.source_id()
            )));
        }
        Ok(ReferenceSourceDefinition {
            source_id: ReferenceSourceId::new(self.source_id())?,
            provider_id: Provider::new(self.provider())?,
            scope,
            desired_state,
            connection_id,
            sync_policy: self.sync_policy(),
        })
    }

    pub(crate) fn builtin_definition(self) -> ReferenceResult<ReferenceSourceDefinition> {
        self.definition(SourceScope::global(), SourceDesiredState::Enabled, None)
    }
}

#[cfg(test)]
pub(crate) fn reference_source_definition(
    source_id: &str,
) -> ReferenceResult<ReferenceSourceDefinition> {
    if let Some(binding) = ReferenceSourceBinding::from_source_id(source_id) {
        return binding.builtin_definition();
    }
    ReferenceSourceDefinition::runtime_default(source_id)
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::{BinanceReferenceSource, MassiveReferenceSource, ReferenceSourceBinding};
    use crate::domain::{SourceConnectionId, SourceDesiredState, SourceScope, SourceSyncPolicy};

    #[test]
    fn supported_bindings_have_unique_owner_derived_identities() {
        let source_ids = ReferenceSourceBinding::ALL
            .into_iter()
            .map(ReferenceSourceBinding::source_id)
            .collect::<BTreeSet<_>>();

        assert_eq!(source_ids.len(), ReferenceSourceBinding::ALL.len());
        for binding in ReferenceSourceBinding::ALL {
            let definition = binding.builtin_definition().unwrap();
            assert_eq!(definition.source_id.as_str(), binding.source_id());
            assert_eq!(definition.provider_id.as_str(), binding.provider());
            assert_eq!(definition.sync_policy, binding.sync_policy());
        }
        assert_eq!(
            ReferenceSourceBinding::Massive(MassiveReferenceSource::Options).sync_policy(),
            SourceSyncPolicy::ScopedSnapshot
        );
    }

    #[test]
    fn advanced_binding_rejects_unsupported_scope_and_accepts_connection_binding() {
        assert!(
            ReferenceSourceBinding::Okx(super::OkxProduct::Spot)
                .definition(
                    SourceScope::underlying_instrument("instrument:equity:US:SPY:common"),
                    SourceDesiredState::Enabled,
                    None,
                )
                .is_err()
        );
        let connected = ReferenceSourceBinding::Binance(BinanceReferenceSource::Spot)
            .definition(
                SourceScope::global(),
                SourceDesiredState::Enabled,
                Some(SourceConnectionId::new("binance-default").unwrap()),
            )
            .unwrap();
        assert_eq!(connected.connection_id.as_deref(), Some("binance-default"));
    }
}
