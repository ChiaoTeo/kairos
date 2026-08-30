//! Results of Actor-owned fill state transitions.

use crate::domain::{ExecutionEvent, ExecutionFill, ExecutionOrder};

pub(crate) enum FillTransition {
    Duplicate {
        order: ExecutionOrder,
        cursor_changed: bool,
    },
    Conflict(ExecutionFill),
    Applied {
        order: ExecutionOrder,
        fill: ExecutionFill,
        event: ExecutionEvent,
    },
}
