//! Cross-process service runtime constants.
//!
//! These values are part of the system contract. They intentionally contain
//! no business types and no transport implementation.

pub const RUNTIME_PROTOCOL_VERSION: &str = "v1";

pub const READY_STATUS: &str = "ready";
pub const DEGRADED_STATUS: &str = "degraded";
pub const STOPPING_STATUS: &str = "stopping";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserves_the_runtime_protocol_version() {
        assert_eq!(RUNTIME_PROTOCOL_VERSION, "v1");
    }
}
