//! Transport-neutral control response returned by the Actor task.

use serde_json::Value;

pub(crate) struct MarketHttpResponse {
    pub(crate) status: u16,
    pub(crate) payload: Value,
}
