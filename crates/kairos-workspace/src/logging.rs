//! Process logging shared by all long-lived Rust components.
//!
//! Logs are emitted as JSONL to stderr. The system supervisor redirects that
//! stream to the component log file, while direct invocations remain visible
//! in a terminal. `RUST_LOG` controls the level/filter (default: `info`).

use tracing_subscriber::{fmt, layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

/// Install the process-wide structured logger. Repeated calls are harmless so
/// tests and embedded callers can initialize logging without coordination.
pub fn init(component: &'static str) {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let result = tracing_subscriber::registry()
        .with(filter)
        .with(
            fmt::layer()
                .json()
                .with_target(true)
                .with_thread_ids(true)
                .with_thread_names(true)
                .with_ansi(false),
        )
        .try_init();
    if result.is_ok() {
        tracing::info!(
            component,
            event = "logger_initialized",
            "structured logging enabled"
        );
    }
}
