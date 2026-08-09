mod process;
mod query;
mod runtime;
mod service;
pub mod wire {
    pub use kairos_market_contract::reference::{decode_reference_changed, ReferenceChangeNotice};
}

pub use crate::domain::snapshot::{MarketSnapshot, ReconcileResult, SubscriptionState};
pub use process::{MarketProcess, MarketSnapshotPublisher, ReferenceChangeSource, ReferenceEvent};
pub use query::{MarketObservationResult, MarketQueryResult};
pub use runtime::MarketRuntime;
pub use service::{MarketApplication, MarketError};
