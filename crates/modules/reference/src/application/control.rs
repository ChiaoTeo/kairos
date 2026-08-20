//! Stable control/query paths shared by the Reference server and CLI.

pub use kairos_reference_contract::control::{
    ASSETS, INSTRUMENTS, LISTINGS, OPTIONS_COVERAGE_ADD, OPTIONS_COVERAGE_REMOVE, PUBLISH, REFRESH,
    SOURCE_PAUSE, SOURCE_RESUME,
};
pub use kairos_workspace::runtime::{HEALTH_PATH as HEALTH, STOP_PATH as STOP};
