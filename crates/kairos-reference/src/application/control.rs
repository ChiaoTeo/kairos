//! Stable control/query paths shared by the Reference server and CLI.

pub use kairos_workspace::runtime::{
    HEALTH_PATH as HEALTH, SNAPSHOT_PATH as SNAPSHOT, STOP_PATH as STOP,
};
pub const MARKETS: &str = "/v1/markets";
pub const RESOLVE_MARKET: &str = "/v1/markets/resolve";
pub const REFRESH: &str = "/v1/refresh";
pub const PUBLISH: &str = "/v1/publish";
pub const ASSETS: &str = "/v1/assets";
pub const QUERY: &str = "/v1/query";
pub const SHOW: &str = "/v1/show";
pub const EVENTS: &str = "/v1/events";
pub const PROVIDERS: &str = "/v1/providers";
