//! Results of Actor-owned fill state transitions.

use crate::application::ExecutionEvent;
use crate::domain::{ExecutionFill, ExecutionOrder};

pub(crate) enum FillTransition {
    Duplicate(ExecutionOrder),
    Conflict(ExecutionFill),
    Applied {
        order: ExecutionOrder,
        fill: ExecutionFill,
        event: ExecutionEvent,
    },
}
