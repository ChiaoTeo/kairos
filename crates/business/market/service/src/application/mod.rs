mod facade;
mod process;
mod query;
pub mod replay;
mod service;
pub mod wire {
    pub use kairos_market_contract::reference::{decode_reference_changed, ReferenceChangeNotice};
}

pub use crate::domain::snapshot::{
    MarketCurrentFreshness, MarketCurrentView, MarketSnapshot, ReconcileResult, SubscriptionState,
};
pub(crate) use facade::source_accepts;
pub use facade::MarketApplication;
pub(crate) use process::MarketProcessSettings;
pub use process::{MarketProcess, MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
pub use query::{ExecutionEstimate, MarketObservationResult, MarketQueryResult, OrderBookSide};
pub use replay::{load_replay_events, load_replay_events_many};
pub use service::MarketError;
