//! Stable control/query paths shared by the Reference server and CLI.

pub use kairos_workspace::runtime::{HEALTH_PATH as HEALTH, STOP_PATH as STOP};
pub const REFRESH: &str = "/v1/refresh";
pub const PUBLISH: &str = "/v1/publish";
pub const ASSETS: &str = "/v1/assets";
pub const INSTRUMENTS: &str = "/v1/instruments";
pub const LISTINGS: &str = "/v1/listings";
pub const SOURCE_PAUSE: &str = "/v1/sources/pause";
pub const SOURCE_RESUME: &str = "/v1/sources/resume";
pub const OPTIONS_COVERAGE_ADD: &str = "/v1/options/coverage/add";
pub const OPTIONS_COVERAGE_REMOVE: &str = "/v1/options/coverage/remove";
