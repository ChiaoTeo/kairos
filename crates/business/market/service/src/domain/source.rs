use kairos_domain_types::{Exchange, MarketId};
use serde::{Deserialize, Serialize};

/// Stable Market-owned identity for one configured external data source.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SourceId(String);

impl SourceId {
    pub fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into().trim().to_ascii_lowercase();
        if value.is_empty() {
            return Err("market source id is required".into());
        }
        if value.chars().any(char::is_whitespace) {
            return Err("market source id must not contain whitespace".into());
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Stable, provider-neutral route identity used to coalesce concurrent
/// subscription activation requests. Instrument symbols are deliberately not
/// part of this key; one source can serve many members on the same route.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct SourceRouteKey {
    pub source_id: Option<String>,
    pub exchange: String,
    pub market_type: String,
    pub asset_type: Option<String>,
}

impl SourceRouteKey {
    pub fn from_market(market: &crate::domain::market::MarketDescriptor) -> Self {
        Self {
            source_id: market.source_id.clone(),
            exchange: market.exchange_id.to_string().to_ascii_lowercase(),
            market_type: market.market_type.to_ascii_lowercase(),
            asset_type: market
                .asset_type
                .as_ref()
                .map(|value| value.to_ascii_lowercase()),
        }
    }
}

impl std::fmt::Display for SourceId {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceStatus {
    #[default]
    Starting,
    Ready,
    Paused,
    Reconnecting,
    WarmingUp,
    Degraded,
    Stopped,
}

/// Provider-neutral failure classification retained in Market health. The
/// provider detail remains diagnostic text, while callers can make decisions
/// without parsing an SDK/error string.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceFailureKind {
    InvalidRequest,
    NotReady,
    Unsupported,
    Authentication,
    Authorization,
    Entitlement,
    RateLimited,
    Transport,
    InvalidPayload,
    SequenceGap,
    ResyncRequired,
    Backpressure,
    Unavailable,
    Replay,
    Shutdown,
    Other,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketReadiness {
    #[default]
    Starting,
    Ready,
    Degraded,
    Stopped,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct SourceEpoch(u64);

impl SourceEpoch {
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    pub const fn get(self) -> u64 {
        self.0
    }

    pub fn advance(&mut self) -> Self {
        self.0 = self.0.saturating_add(1);
        *self
    }
}

/// Market route facts used to match canonical subscription members to one
/// configured source. Provider connection details deliberately do not live
/// here.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceDescriptor {
    pub id: SourceId,
    pub exchange_id: Option<Exchange>,
    pub market_type: Option<String>,
    pub asset_type: Option<String>,
}

impl SourceDescriptor {
    pub fn new(
        id: SourceId,
        exchange_id: Exchange,
        market_type: impl Into<String>,
        asset_type: Option<String>,
    ) -> Result<Self, String> {
        let market_type = market_type.into().trim().to_ascii_lowercase();
        if market_type.is_empty() {
            return Err("market source market type is required".into());
        }
        let asset_type = asset_type
            .map(|value| value.trim().to_ascii_lowercase())
            .filter(|value| !value.is_empty());
        Ok(Self {
            id,
            exchange_id: Some(exchange_id),
            market_type: Some(market_type),
            asset_type,
        })
    }

    /// A finite source whose recorded observations can span provider routes.
    pub fn all_routes(id: SourceId) -> Self {
        Self {
            id,
            exchange_id: None,
            market_type: None,
            asset_type: None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceState {
    pub descriptor: SourceDescriptor,
    pub status: SourceStatus,
    pub epoch: SourceEpoch,
    pub last_error: Option<String>,
    #[serde(default)]
    pub last_failure_kind: Option<SourceFailureKind>,
    pub resyncing_markets: Vec<MarketId>,
}

impl SourceState {
    pub fn starting(descriptor: SourceDescriptor) -> Self {
        Self {
            descriptor,
            status: SourceStatus::Starting,
            epoch: SourceEpoch::default(),
            last_error: None,
            last_failure_kind: None,
            resyncing_markets: Vec::new(),
        }
    }

    pub fn change_status(
        &mut self,
        epoch: SourceEpoch,
        status: SourceStatus,
        error: Option<String>,
    ) -> bool {
        if epoch < self.epoch {
            return false;
        }
        self.epoch = epoch;
        self.status = status;
        if let Some(error) = error {
            self.last_error = Some(error);
            self.last_failure_kind = Some(SourceFailureKind::Other);
        } else if matches!(status, SourceStatus::Ready | SourceStatus::Stopped) {
            self.last_error = None;
            self.last_failure_kind = None;
        }
        true
    }

    pub fn fail(&mut self, epoch: SourceEpoch, kind: SourceFailureKind, error: String) -> bool {
        if epoch < self.epoch {
            return false;
        }
        self.epoch = epoch;
        self.status = SourceStatus::Degraded;
        self.last_error = Some(error);
        self.last_failure_kind = Some(kind);
        true
    }
}

pub fn derive_readiness<'a>(sources: impl IntoIterator<Item = &'a SourceState>) -> MarketReadiness {
    let sources = sources.into_iter().collect::<Vec<_>>();
    if sources.is_empty() {
        // A demand-driven live process is healthy before the first
        // subscription activates a provider source. Source readiness is
        // reported once a source exists; an empty source set is not a
        // startup failure.
        return MarketReadiness::Ready;
    }
    if sources
        .iter()
        .all(|state| state.status == SourceStatus::Stopped)
    {
        MarketReadiness::Stopped
    } else if sources
        .iter()
        .all(|state| matches!(state.status, SourceStatus::Ready | SourceStatus::Paused))
    {
        MarketReadiness::Ready
    } else if sources
        .iter()
        .any(|state| matches!(state.status, SourceStatus::Degraded | SourceStatus::Stopped))
    {
        MarketReadiness::Degraded
    } else {
        MarketReadiness::Starting
    }
}

#[cfg(test)]
mod tests {
    use super::{
        derive_readiness, MarketReadiness, SourceEpoch, SourceId, SourceState, SourceStatus,
    };
    use crate::domain::source::SourceDescriptor;
    use kairos_domain_types::Exchange;

    fn state() -> SourceState {
        SourceState::starting(
            SourceDescriptor::new(
                SourceId::new("Binance.Spot").unwrap(),
                Exchange::new("binance").unwrap(),
                "spot",
                Some("crypto".into()),
            )
            .unwrap(),
        )
    }

    #[test]
    fn source_identity_is_normalized_and_validated() {
        assert_eq!(
            SourceId::new(" Binance.Spot ").unwrap().as_str(),
            "binance.spot"
        );
        assert!(SourceId::new(" ").is_err());
        assert!(SourceId::new("binance spot").is_err());
    }

    #[test]
    fn empty_demand_driven_market_is_ready_for_control_plane() {
        assert_eq!(derive_readiness(std::iter::empty()), MarketReadiness::Ready);
    }

    #[test]
    fn stale_epoch_cannot_change_source_state() {
        let mut state = state();
        assert!(state.change_status(SourceEpoch::new(2), SourceStatus::Ready, None));
        assert!(!state.change_status(
            SourceEpoch::new(1),
            SourceStatus::Degraded,
            Some("stale failure".into())
        ));
        assert_eq!(state.status, SourceStatus::Ready);
        assert_eq!(state.epoch.get(), 2);
    }

    #[test]
    fn all_active_sources_define_source_readiness() {
        let mut ready = state();
        let mut degraded = state();
        ready.change_status(SourceEpoch::new(1), SourceStatus::Ready, None);
        degraded.change_status(
            SourceEpoch::new(1),
            SourceStatus::Degraded,
            Some("offline".into()),
        );
        assert_eq!(
            derive_readiness([&ready, &degraded]),
            MarketReadiness::Degraded
        );
    }

    #[test]
    fn typed_failure_survives_reconnecting_and_clears_when_ready() {
        let mut state = state();
        state.fail(
            SourceEpoch::new(1),
            super::SourceFailureKind::Transport,
            "controlled disconnect".into(),
        );
        state.change_status(SourceEpoch::new(1), SourceStatus::Reconnecting, None);
        assert_eq!(
            state.last_failure_kind,
            Some(super::SourceFailureKind::Transport)
        );
        assert_eq!(state.last_error.as_deref(), Some("controlled disconnect"));

        state.change_status(SourceEpoch::new(2), SourceStatus::Ready, None);
        assert_eq!(state.last_failure_kind, None);
        assert_eq!(state.last_error, None);
    }
}
