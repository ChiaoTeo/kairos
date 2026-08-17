//! Typed ingress messages delivered from transport to the Actor task.

use tokio::sync::oneshot;

use super::MarketHttpResponse;

pub(crate) struct MarketHttpRequest {
    pub(crate) method: String,
    pub(crate) path: String,
    pub(crate) body: Vec<u8>,
    pub(crate) response: oneshot::Sender<MarketHttpResponse>,
}

pub(crate) enum EngineCommand {
    Http(MarketHttpRequest),
    ReconcileMarketUniverse(crate::application::ReconcileMarketUniverse),
}
