//! Private messages between the single Market Actor and Integration I/O
//! drivers. These are Market runtime mechanics, not application API types.

use kairos_domain_types::{InstrumentId, MarketId, Sequence, UnixNanos};

use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::orderbook::PriceLevel;
use crate::domain::source::{SourceEpoch, SourceFailureKind, SourceId, SourceStatus};

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) struct SourceRequestId(u64);

impl SourceRequestId {
    pub(crate) const fn new(value: u64) -> Self {
        Self(value)
    }
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(crate) struct ProviderSubscriptionId(String);

impl ProviderSubscriptionId {
    pub(crate) fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err("provider subscription id is required".into());
        }
        Ok(Self(value))
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Debug)]
pub(crate) enum SourceCommand {
    Subscribe {
        request_id: SourceRequestId,
        market: Box<MarketDescriptor>,
    },
    Unsubscribe {
        request_id: SourceRequestId,
        handle: ProviderSubscriptionId,
    },
    ResyncOrderBook {
        request_id: SourceRequestId,
        market: Box<MarketDescriptor>,
    },
    Pause,
    Resume,
    Reconnect,
    Shutdown,
}

#[derive(Debug)]
pub(crate) enum SourceInput {
    StatusChanged {
        source_id: SourceId,
        epoch: SourceEpoch,
        status: SourceStatus,
        error: Option<String>,
    },
    SubscriptionConfirmed {
        source_id: SourceId,
        epoch: SourceEpoch,
        request_id: SourceRequestId,
        handle: ProviderSubscriptionId,
    },
    SubscriptionRejected {
        source_id: SourceId,
        epoch: SourceEpoch,
        request_id: SourceRequestId,
        error: String,
    },
    Unsubscribed {
        source_id: SourceId,
        epoch: SourceEpoch,
        request_id: SourceRequestId,
    },
    Observation {
        source_id: SourceId,
        epoch: SourceEpoch,
        observation: MarketObservation,
    },
    ReplayObservation {
        source_id: SourceId,
        epoch: SourceEpoch,
        observation: MarketObservation,
        accepted:
            tokio::sync::oneshot::Sender<Result<crate::domain::snapshot::MarketSnapshot, String>>,
    },
    OrderBook {
        source_id: SourceId,
        epoch: SourceEpoch,
        update: SourceOrderBookUpdate,
    },
    ResyncRequired {
        source_id: SourceId,
        epoch: SourceEpoch,
        market: Box<MarketDescriptor>,
        reason: String,
    },
    ResyncCompleted {
        source_id: SourceId,
        epoch: SourceEpoch,
        request_id: SourceRequestId,
        market_id: MarketId,
    },
    ResyncRejected {
        source_id: SourceId,
        epoch: SourceEpoch,
        request_id: SourceRequestId,
        market_id: MarketId,
        error: String,
    },
    Failed {
        source_id: SourceId,
        epoch: SourceEpoch,
        kind: SourceFailureKind,
        error: String,
    },
    Completed {
        source_id: SourceId,
        epoch: SourceEpoch,
    },
}

#[derive(Debug)]
pub(crate) struct SourceOrderBookUpdate {
    pub(crate) market: Box<MarketDescriptor>,
    pub(crate) source_id: String,
    pub(crate) market_id: MarketId,
    pub(crate) instrument_id: InstrumentId,
    pub(crate) first_sequence: Sequence,
    pub(crate) last_sequence: Sequence,
    pub(crate) event_time_unix_nanos: UnixNanos,
    pub(crate) bids: Vec<PriceLevel>,
    pub(crate) asks: Vec<PriceLevel>,
    pub(crate) snapshot: bool,
}
