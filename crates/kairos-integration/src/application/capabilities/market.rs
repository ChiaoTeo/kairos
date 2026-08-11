//! Market snapshot, live-channel, and historical-data capabilities.

mod snapshot {
    //! Market interaction protocols.

    use std::future::Future;

    use kairos_domain_types::ProviderSymbol;

    pub use super::super::market_facts::{MarketEvent, MarketEventKind};
    use crate::application::IntegrationError;

    /// A stateless provider snapshot operation. Subscription ownership and polling
    /// policy remain in the Market business; this capability only fetches the
    /// requested provider symbols.
    pub trait MarketSnapshotConnection: Send {
        fn fetch_snapshot(
            &mut self,
            symbols: &[ProviderSymbol],
        ) -> Result<Vec<MarketEvent>, IntegrationError>;
    }

    /// Async-first snapshot operation, polled by the caller's runtime.
    pub trait AsyncMarketSnapshotConnection: Send {
        fn fetch_snapshot(
            &mut self,
            symbols: &[ProviderSymbol],
        ) -> impl Future<Output = Result<Vec<MarketEvent>, IntegrationError>> + Send;
    }
}

pub use snapshot::*;

mod live {
    //! Provider-neutral market-stream interaction protocol.
    //!
    //! The protocol models the interaction pattern, not an exchange API.  A
    //! Binance websocket, an IBKR stream, a replay source, and a REST polling
    //! adapter may all implement it without pretending that their provider APIs
    //! are otherwise identical.

    use super::super::market_facts::{MarketEvent, MarketStreamCapabilities};
    use std::future::Future;

    use crate::application::IntegrationError;

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

    pub trait MarketStreamConnection: Send {
        fn descriptor(&self) -> &crate::domain::ConnectionDescriptor;
        fn connect_channel(&mut self) -> Result<(), IntegrationError>;
        fn disconnect_channel(&mut self) -> Result<(), IntegrationError>;
        fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.disconnect_channel()?;
            self.connect_channel()
        }
        fn channel_health(&self) -> crate::domain::ConnectionHealth;

        fn capabilities(&self) -> MarketStreamCapabilities {
            MarketStreamCapabilities::default()
        }

        fn subscribe(
            &mut self,
            request: MarketSubscription,
        ) -> Result<SubscriptionId, IntegrationError>;

        fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError>;

        fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError>;
    }

    /// Async-first live market channel. The caller runtime owns polling and task
    /// scheduling; absence is not modeled as `Option`, preventing busy polling.
    pub trait AsyncMarketEventSource: Send {
        fn connect_channel(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn disconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn reconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send {
            async move {
                self.disconnect_channel().await?;
                self.connect_channel().await
            }
        }
        fn channel_health(&self) -> crate::domain::ConnectionHealth;
        fn subscribe(
            &mut self,
            request: MarketSubscription,
        ) -> impl Future<Output = Result<SubscriptionId, IntegrationError>> + Send;
        fn unsubscribe(
            &mut self,
            subscription: SubscriptionId,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn next_market_event(
            &mut self,
        ) -> impl Future<Output = Result<MarketEvent, IntegrationError>> + Send;
    }
}

pub use live::*;

mod historical {
    //! Provider-neutral historical market-data access.
    //!
    //! Historical downloads are deliberately separate from `MarketStreamConnection`:
    //! a REST backfill has a bounded time window and must be resumable, while a
    //! stream is an open-ended live capability.

    use super::super::market_facts::{MarketDataKind, MarketEvent, MarketStreamCapabilities};
    use crate::application::IntegrationError;
    use kairos_domain_types::{Symbol, UnixNanos};
    use std::future::Future;

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct HistoricalMarketRequest {
        pub symbol: Symbol,
        pub data_kind: MarketDataKind,
        pub start_time_unix_nanos: UnixNanos,
        pub end_time_unix_nanos: UnixNanos,
        pub interval: Option<String>,
        pub adjusted: Option<bool>,
    }

    impl HistoricalMarketRequest {
        pub fn validate(&self) -> Result<(), IntegrationError> {
            if self.end_time_unix_nanos < self.start_time_unix_nanos {
                return Err(IntegrationError::InvalidRequest(
                    "historical market time window is invalid".into(),
                ));
            }
            if matches!(
                self.data_kind,
                MarketDataKind::Bar | MarketDataKind::TradeBar
            ) && self
                .interval
                .as_deref()
                .is_none_or(|value| value.trim().is_empty())
            {
                return Err(IntegrationError::InvalidRequest(
                    "historical bars require an interval".into(),
                ));
            }
            Ok(())
        }
    }

    pub trait HistoricalMarketDataConnection: Send {
        fn capabilities(&self) -> MarketStreamCapabilities;

        /// Fetches and normalizes the complete requested window. Implementations
        /// own provider pagination and rate-limit handling.
        fn fetch(
            &mut self,
            request: &HistoricalMarketRequest,
        ) -> Result<Vec<MarketEvent>, IntegrationError>;
    }

    pub trait AsyncHistoricalMarketDataConnection: Send {
        fn capabilities(&self) -> MarketStreamCapabilities;

        fn fetch(
            &mut self,
            request: &HistoricalMarketRequest,
        ) -> impl Future<Output = Result<Vec<MarketEvent>, IntegrationError>> + Send;
    }

    #[cfg(test)]
    mod tests {
        use super::{HistoricalMarketRequest, MarketDataKind};
        use kairos_domain_types::{Symbol, UnixNanos};

        fn request() -> HistoricalMarketRequest {
            HistoricalMarketRequest {
                symbol: Symbol::new("BTCUSDT").unwrap(),
                data_kind: MarketDataKind::Bar,
                start_time_unix_nanos: UnixNanos::new(1),
                end_time_unix_nanos: UnixNanos::new(2),
                interval: Some("1m".into()),
                adjusted: None,
            }
        }

        #[test]
        fn rejects_invalid_windows() {
            let mut value = request();
            value.end_time_unix_nanos = UnixNanos::new(0);
            assert!(value.validate().is_err());
        }

        #[test]
        fn bars_require_interval() {
            let mut value = request();
            value.interval = None;
            assert!(value.validate().is_err());
        }
    }
}

pub use historical::*;
