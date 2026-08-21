//! Shared JSON-RPC control-boundary mechanics.
//!
//! Business modules own their typed requests and responses. Platform runtimes
//! own listeners, sessions, queues, and lifecycle.  The shared protocol layer
//! exposes only JSON-RPC helpers and error conventions; concrete transport
//! adapters live in the owning platform crate.

/// jsonrpsee-backed control service helpers.
///
/// Business contract crates define control services with
/// [`conflux_rpc`](crate::control::jsonrpc::conflux_rpc). Runtimes such as
/// Conflux adapt the generated server trait into their own actor ingress
/// instead of owning the business protocol definition.
pub mod jsonrpc {
    pub use jsonrpsee::core::{RpcResult, async_trait};
    pub use jsonrpsee::proc_macros::rpc;
    pub use jsonrpsee::types::ErrorObjectOwned;
    pub use kairos_protocol_macros::conflux_rpc;

    pub const NOT_SENT_CODE: i32 = -32_001;
    pub const RESULT_UNKNOWN_CODE: i32 = -32_002;
    pub const ACTOR_STOPPED_CODE: i32 = -32_003;
    pub const READINESS_REJECTED_CODE: i32 = -32_004;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub enum ControlRuntimeFailure {
        NotSent,
        ResultUnknown,
        ActorStopped,
        ReadinessRejected,
    }

    impl ControlRuntimeFailure {
        pub fn code(self) -> i32 {
            match self {
                Self::NotSent => NOT_SENT_CODE,
                Self::ResultUnknown => RESULT_UNKNOWN_CODE,
                Self::ActorStopped => ACTOR_STOPPED_CODE,
                Self::ReadinessRejected => READINESS_REJECTED_CODE,
            }
        }

        pub fn reason(self) -> &'static str {
            match self {
                Self::NotSent => "not_sent",
                Self::ResultUnknown => "result_unknown",
                Self::ActorStopped => "actor_stopped",
                Self::ReadinessRejected => "readiness_rejected",
            }
        }

        pub fn message(self) -> &'static str {
            match self {
                Self::NotSent => "control request was not submitted",
                Self::ResultUnknown => "control request result is unknown",
                Self::ActorStopped => "control actor stopped",
                Self::ReadinessRejected => "control readiness was rejected",
            }
        }

        pub fn into_error(self) -> ErrorObjectOwned {
            ErrorObjectOwned::owned(self.code(), self.message(), Some(self.reason()))
        }
    }

    pub fn runtime_error(failure: ControlRuntimeFailure) -> ErrorObjectOwned {
        failure.into_error()
    }

    pub fn business_error(
        code: i32,
        message: impl Into<String>,
        details: impl serde::Serialize,
    ) -> ErrorObjectOwned {
        ErrorObjectOwned::owned(code, message.into(), Some(details))
    }
}
