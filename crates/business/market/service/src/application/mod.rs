mod ports;
mod process;
mod query;
pub mod replay;
mod runtime;
mod service;
pub mod wire {
    pub use kairos_market_contract::reference::{decode_reference_changed, ReferenceChangeNotice};
}

pub use crate::domain::snapshot::{MarketSnapshot, ReconcileResult, SubscriptionState};
pub use ports::{MarketDataKey, MarketFeed, MarketFeedRoute, MarketOrderBookUpdate};
pub use process::{MarketProcess, MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
pub use query::{ExecutionEstimate, MarketObservationResult, MarketQueryResult, OrderBookSide};
pub use replay::{load_replay_events, load_replay_events_many};
pub use runtime::MarketRuntime;
pub use service::{MarketApplication, MarketError};
