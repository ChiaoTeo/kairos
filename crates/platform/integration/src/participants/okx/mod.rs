//! Concrete OKX connections organized by authentication boundary and transport.

mod config;
pub mod private;
pub mod public;

pub use config::{
    OkxCredential, OkxPrivateRestConfig, OkxPrivateWebSocketConfig, OkxRestConfig,
    OkxWebSocketConfig,
};
