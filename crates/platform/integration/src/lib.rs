//! Exchange, broker, and data-provider integration boundary.
//!
//! Participant-neutral capabilities live under [`capabilities`]. Participant-native
//! construction lives under [`participants`]. Explicit synchronous projections
//! live under [`blocking`].

pub mod blocking;
pub mod capabilities;
pub mod composition;
pub mod domain;
mod error;
pub mod participants;
pub(crate) mod services;
pub(crate) mod transport;

pub use capabilities::account::*;
pub use capabilities::connection::*;
pub use capabilities::earn::*;
pub use capabilities::event::*;
pub use capabilities::execution::*;
pub use capabilities::fees::*;
pub use capabilities::market::*;
pub use capabilities::reference::*;
pub use capabilities::transfer::*;
pub use domain::*;
pub use error::{CommandResult, IntegrationError};
