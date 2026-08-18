//! Explicit synchronous projections for dedicated blocking workers.
//!
//! Do not call these APIs from a Tokio worker. Async-first capabilities remain
//! at the crate root; synchronous callers opt into this namespace explicitly.

pub mod account;
pub mod connection;
pub mod event;
pub mod execution;
pub mod funding;
pub mod market;
pub mod reference;

pub use account::*;
pub use connection::*;
pub use event::*;
pub use execution::*;
pub use funding::*;
pub use market::*;
pub use reference::*;
