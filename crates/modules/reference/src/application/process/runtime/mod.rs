//! Long-running startup, tick, and event-delivery workflows.

mod delivery;
mod startup;
mod state;
mod tick;

pub use delivery::ReferencePublication;
pub(crate) use state::{
    ReferenceAppErrorSummary, ReferenceApplicationPhase, ReferenceApplicationRuntime,
    ReferenceTickTiming, ReferenceTickTrigger,
};
pub use tick::ReferenceRefreshResult;
