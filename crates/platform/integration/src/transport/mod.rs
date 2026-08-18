//! Participant-neutral transport and connection-lifecycle primitives.
//!
//! Provider authentication, payloads, error codes, and channel recovery do
//! not belong here. They remain under `services::participants`.

pub(crate) mod http;
pub(crate) mod websocket;
