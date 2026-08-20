mod identity;
mod readiness;
mod route;
mod state;

pub use identity::{SourceEpoch, SourceId};
pub use readiness::{MarketReadiness, derive_readiness};
pub use route::{SourceDescriptor, SourceRouteKey};
pub use state::{SourceFailureKind, SourceState, SourceStatus};

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::Exchange;

    use super::{
        MarketReadiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceState,
        SourceStatus, derive_readiness,
    };

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
            SourceFailureKind::Transport,
            "controlled disconnect".into(),
        );
        state.change_status(SourceEpoch::new(1), SourceStatus::Reconnecting, None);
        assert_eq!(state.last_failure_kind, Some(SourceFailureKind::Transport));
        assert_eq!(state.last_error.as_deref(), Some("controlled disconnect"));

        state.change_status(SourceEpoch::new(2), SourceStatus::Ready, None);
        assert_eq!(state.last_failure_kind, None);
        assert_eq!(state.last_error, None);
    }
}
