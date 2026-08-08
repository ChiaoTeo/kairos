//! Cross-process service runtime constants.
//!
//! These values are part of the system contract. They intentionally contain
//! no business types and no transport implementation.

pub const RUNTIME_PROTOCOL_VERSION: &str = "v1";
pub const HEALTH_PATH: &str = "/v1/health";
pub const SNAPSHOT_PATH: &str = "/v1/snapshot";
pub const STOP_PATH: &str = "/v1/stop";

pub const READY_STATUS: &str = "ready";
pub const DEGRADED_STATUS: &str = "degraded";
pub const STOPPING_STATUS: &str = "stopping";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserves_the_control_paths() {
        assert_eq!(RUNTIME_PROTOCOL_VERSION, "v1");
        assert_eq!(HEALTH_PATH, "/v1/health");
        assert_eq!(SNAPSHOT_PATH, "/v1/snapshot");
        assert_eq!(STOP_PATH, "/v1/stop");
    }
}
