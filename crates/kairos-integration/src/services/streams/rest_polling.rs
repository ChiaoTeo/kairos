//! REST-backed implementation of the market-stream protocol.
//!
//! This is useful when a provider has no websocket implementation yet, for
//! deterministic integration tests, and for replay-like polling.  It exposes
//! stream semantics while making the snapshot-to-event limitation explicit.

use std::collections::{HashSet, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::connection::Connection;
use crate::application::error::IntegrationError;
use crate::application::market_stream::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::domain::{
    ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState, MarketEvent,
    MarketEventKind,
};

pub trait RestSnapshotReader: Send {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError>;
}

pub struct RestPollingMarketStream<R> {
    identity: ConnectionIdentity,
    state: ConnectionState,
    reader: R,
    subscription: Option<(SubscriptionId, MarketSubscription)>,
    queue: VecDeque<MarketEvent>,
    last_snapshot: HashSet<(String, MarketEventKind, Option<String>, Option<String>)>,
    next_subscription_id: u64,
}

impl<R: RestSnapshotReader> RestPollingMarketStream<R> {
    pub fn new(identity: ConnectionIdentity, reader: R) -> Self {
        Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            reader,
            subscription: None,
            queue: VecDeque::new(),
            last_snapshot: HashSet::new(),
            next_subscription_id: 1,
        }
    }

    fn poll(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let Some((_, subscription)) = self.subscription.as_ref() else {
            return Err(IntegrationError::InvalidRequest(
                "market stream must be subscribed before polling".into(),
            ));
        };
        let events = self.reader.snapshot(&subscription.symbols)?;
        let mut current = HashSet::new();
        for event in events {
            let key = (
                event.symbol.clone(),
                event.kind,
                event.price.clone(),
                event.quantity.clone(),
            );
            current.insert(key.clone());
            if self.last_snapshot.insert(key) {
                self.queue.push_back(event);
            }
        }
        self.last_snapshot = current;
        Ok(())
    }
}

impl<R: RestSnapshotReader> Connection for RestPollingMarketStream<R> {
    fn identity(&self) -> &ConnectionIdentity {
        &self.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = false;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos());
        self.state.last_error = None;
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        self.subscription = None;
        self.queue.clear();
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.reconnect_count += 1;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos());
        self.state.last_error = None;
        self.last_snapshot.clear();
        Ok(())
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl<R: RestSnapshotReader> MarketStreamConnection for RestPollingMarketStream<R> {
    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.subscription = Some((id, request));
        self.last_snapshot.clear();
        Ok(id)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        if self.subscription.as_ref().map(|value| value.0) != Some(subscription) {
            return Err(IntegrationError::InvalidRequest(
                "unknown market subscription".into(),
            ));
        }
        self.subscription = None;
        self.last_snapshot.clear();
        self.queue.clear();
        Ok(())
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        if self.queue.is_empty() && self.subscription.is_some() {
            self.poll()?;
        }
        Ok(self.queue.pop_front())
    }
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::connection::Connection;
    use crate::application::market_stream::MarketStreamConnection;
    use crate::domain::MarketEventKind;
    use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};

    struct Reader {
        calls: usize,
    }

    impl RestSnapshotReader for Reader {
        fn snapshot(&mut self, _: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
            self.calls += 1;
            Ok(vec![MarketEvent {
                symbol: "BTCUSDT".into(),
                kind: MarketEventKind::Quote,
                price: Some("100".into()),
                quantity: Some("1".into()),
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: self.calls as u64,
            }])
        }
    }

    #[test]
    fn polling_reader_has_stream_semantics_and_deduplicates_snapshots() {
        let identity = crate::domain::ConnectionIdentity::new(
            "market.test.rest",
            "test",
            Some(ProductFamily::Spot),
            AccessScope::Public,
            TransportKind::Rest,
            IntegrationCapability::MarketStream,
        )
        .unwrap();
        let mut stream = RestPollingMarketStream::new(identity, Reader { calls: 0 });
        stream.start().unwrap();
        stream
            .subscribe(MarketSubscription::new(["btcusdt"]).unwrap())
            .unwrap();
        stream.poll().unwrap();
        assert!(stream.next_event().unwrap().is_some());
        stream.poll().unwrap();
        assert!(stream.next_event().unwrap().is_none());
    }
}
