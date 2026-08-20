//! Typed data-plane projection models decoded from Reference SQLite rows.

use serde::{Deserialize, Serialize};

pub type ReferenceMarket = super::Market;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceHealth {
    pub status: String,
    pub generation: kairos_primitives::time::Generation,
    pub event_sequence: kairos_primitives::time::Sequence,
}
