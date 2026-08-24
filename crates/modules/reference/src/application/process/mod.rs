//! Reusable process facade split into control and runtime responsibilities.

mod control;
mod runtime;

pub(crate) use runtime::{
    ReferenceAppErrorSummary, ReferenceApplicationPhase, ReferenceApplicationRuntime,
    ReferenceTickTiming, ReferenceTickTrigger,
};
pub use runtime::{ReferencePublication, ReferenceRefreshResult};
