//! Provider-neutral market-stream interaction protocol.
//!
//! The protocol models the interaction pattern, not an exchange API.  A
//! Binance websocket, an IBKR stream, a replay source, and a REST polling
//! adapter may all implement it without pretending that their provider APIs
//! are otherwise identical.

use crate::domain::MarketEvent;

use super::{connection::Connection, error::IntegrationError};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MarketSubscription {
    pub symbols: Vec<String>,
}

impl MarketSubscription {
    pub fn new<I, S>(symbols: I) -> Result<Self, IntegrationError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let symbols: Vec<String> = symbols
            .into_iter()
            .map(Into::into)
            .map(|symbol| symbol.trim().to_ascii_uppercase())
            .filter(|symbol| !symbol.is_empty())
            .collect();
        if symbols.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "market subscription requires at least one symbol".into(),
            ));
        }
        Ok(Self { symbols })
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SubscriptionId(pub u64);

pub trait MarketStreamConnection: Connection {
    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError>;

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError>;

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError>;
}
