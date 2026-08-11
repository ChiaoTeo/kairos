//! REST-backed implementation of the market-stream protocol.
//!
//! This is useful when a provider has no websocket implementation yet, for
//! deterministic integration tests, and for replay-like polling.  It exposes
//! stream semantics while making the snapshot-to-event limitation explicit.

use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::application::capabilities::market::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::application::capabilities::{
    ConnectionDescriptor, ConnectionHealth, ConnectionLifecycle, ConnectionState, MarketEvent,
    MarketEventKind, MarketStreamCapabilities,
};
use crate::application::error::IntegrationError;

pub trait RestSnapshotReader: Send {
    fn snapshot(&mut self, symbols: &[String]) -> Result<Vec<MarketEvent>, IntegrationError>;

    fn capabilities(&self) -> MarketStreamCapabilities {
        MarketStreamCapabilities::default()
    }
}

pub struct RestPollingMarketStream<R> {
    identity: ConnectionDescriptor,
    state: ConnectionState,
    reader: R,
    subscriptions: BTreeMap<SubscriptionId, MarketSubscription>,
    queue: VecDeque<MarketEvent>,
    last_snapshot: HashSet<(
        kairos_domain_types::Symbol,
        MarketEventKind,
        Option<kairos_domain_types::Price>,
        Option<kairos_domain_types::Quantity>,
    )>,
    next_subscription_id: u64,
}

impl<R: RestSnapshotReader> RestPollingMarketStream<R> {
    pub fn new(identity: ConnectionDescriptor, reader: R) -> Self {
        Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            reader,
            subscriptions: BTreeMap::new(),
            queue: VecDeque::new(),
            last_snapshot: HashSet::new(),
            next_subscription_id: 1,
        }
    }

    fn poll(&mut self) -> Result<(), IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        if self.subscriptions.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "market stream must be subscribed before polling".into(),
            ));
        }
        let symbols = self
            .subscriptions
            .values()
            .flat_map(|subscription| subscription.symbols.iter().cloned())
            .collect::<BTreeSet<_>>();
        let events = self
            .reader
            .snapshot(&symbols.into_iter().collect::<Vec<_>>())?;
        let mut current = HashSet::new();
        for event in events {
            let key = (
                event.symbol.clone(),
                event.kind,
                event.price,
                event.quantity,
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

impl<R: RestSnapshotReader> MarketStreamConnection for RestPollingMarketStream<R> {
    fn descriptor(&self) -> &ConnectionDescriptor {
        &self.identity
    }

    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = false;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        self.state.last_error = None;
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        self.subscriptions.clear();
        self.queue.clear();
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.reconnect_count += 1;
        self.state.connected_at_unix_nanos = Some(now_unix_nanos().into());
        self.state.last_error = None;
        self.last_snapshot.clear();
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }

    fn capabilities(&self) -> MarketStreamCapabilities {
        self.reader.capabilities()
    }

    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        let id = SubscriptionId(self.next_subscription_id);
        self.next_subscription_id += 1;
        self.subscriptions.insert(id, request);
        self.last_snapshot.clear();
        Ok(id)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        if self.subscriptions.remove(&subscription).is_none() {
            return Err(IntegrationError::InvalidRequest(
                "unknown market subscription".into(),
            ));
        }
        self.last_snapshot.clear();
        self.queue.clear();
        Ok(())
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        if self.queue.is_empty() && !self.subscriptions.is_empty() {
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
    use crate::application::capabilities::market::MarketStreamConnection;
    use crate::application::capabilities::MarketEventKind;
    use crate::application::capabilities::{ParticipantKind, ParticipantRef};

    struct Reader {
        calls: usize,
    }

    impl RestSnapshotReader for Reader {
        fn snapshot(&mut self, _: &[String]) -> Result<Vec<MarketEvent>, IntegrationError> {
            self.calls += 1;
            Ok(vec![MarketEvent {
                symbol: kairos_domain_types::Symbol::new("BTCUSDT").unwrap(),
                kind: MarketEventKind::Quote,
                price: Some("100".parse().unwrap()),
                quantity: Some("1".parse().unwrap()),
                rate: None,
                ask_price: None,
                ask_quantity: None,
                bids: Vec::new(),
                asks: Vec::new(),
                bar: None,
                greeks: None,
                first_sequence: None,
                last_sequence: None,
                sequence: None,
                observed_at_unix_nanos: (self.calls as u64).into(),
            }])
        }
    }

    #[test]
    fn polling_reader_has_stream_semantics_and_deduplicates_snapshots() {
        let identity = crate::domain::ConnectionDescriptor::new(
            "market.test.rest",
            ParticipantRef::new(ParticipantKind::Exchange, "test").unwrap(),
            "market-data",
        )
        .unwrap();
        let mut stream = RestPollingMarketStream::new(identity, Reader { calls: 0 });
        stream.connect_channel().unwrap();
        stream
            .subscribe(MarketSubscription::new(["btcusdt"]).unwrap())
            .unwrap();
        stream.poll().unwrap();
        assert!(stream.next_event().unwrap().is_some());
        stream.poll().unwrap();
        assert!(stream.next_event().unwrap().is_none());
    }

    #[test]
    fn polling_reader_keeps_multiple_subscriptions_on_one_connection() {
        let identity = crate::domain::ConnectionDescriptor::new(
            "market.test.rest",
            ParticipantRef::new(ParticipantKind::Exchange, "test").unwrap(),
            "market-data",
        )
        .unwrap();
        let mut stream = RestPollingMarketStream::new(identity, Reader { calls: 0 });
        stream.connect_channel().unwrap();
        let first = stream
            .subscribe(MarketSubscription::new(["btcusdt"]).unwrap())
            .unwrap();
        let second = stream
            .subscribe(MarketSubscription::new(["ethusdt"]).unwrap())
            .unwrap();
        stream.poll().unwrap();
        assert!(stream.next_event().unwrap().is_some());
        stream.unsubscribe(first).unwrap();
        stream.unsubscribe(second).unwrap();
        assert!(stream.next_event().unwrap().is_none());
    }
}
