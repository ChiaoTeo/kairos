//! Exchange, broker, and data-provider integration boundary.
//!
//! Provider-neutral capabilities live under [`application`]. Participant-native
//! construction lives under [`participants`]. Explicit synchronous projections
//! live under [`blocking`].

pub mod application;
pub mod composition;
pub mod domain;
pub(crate) mod services;

pub use application::{blocking, participants};
