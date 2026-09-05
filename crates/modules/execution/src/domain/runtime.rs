use kairos_primitives::time::UnixNanos;

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum ExecutionRuntimeError {
    #[error(
        "execution business time cannot move backwards: current={current}, requested={requested}"
    )]
    BusinessTimeRegression {
        current: UnixNanos,
        requested: UnixNanos,
    },
}

impl ExecutionRuntimeError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::BusinessTimeRegression { .. } => "execution.runtime.business_time_regression",
        }
    }
}
