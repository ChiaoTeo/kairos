//! Explicit synchronous facades for dedicated blocking workers.
//!
//! Do not call these APIs from a Tokio worker. Async-first capabilities remain
//! at the crate root; synchronous callers opt into this namespace explicitly.

pub mod account;
pub mod connection;
pub mod earn;
pub mod event;
pub mod execution;
pub mod market;
pub mod reference;
pub mod transfer;

pub use account::*;
pub use connection::*;
pub use earn::*;
pub use event::*;
pub use execution::*;
pub use market::*;
pub use reference::*;
pub use transfer::*;
