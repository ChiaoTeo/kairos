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

pub use capabilities::{
    account::*, connection::*, earn::*, event::*, execution::*, market::*, reference::*,
    transfer::*,
};
pub use domain::*;
pub use error::{CommandResult, IntegrationError};
