//! Massive data-provider-native connection facade.

mod config;
mod connection;
mod dividends;
mod market_data;
mod reference;
mod types;

pub use config::{MassiveChannelConfig, MassiveConnectionConfig};
pub use connection::MassiveConnection;
pub use dividends::{MassiveCashDividend, MassiveDividendCatalog};
pub use market_data::{MassiveAsyncHistoricalMarket, MassiveAsyncLiveMarket};
pub use reference::MassiveInstrumentCatalog;
pub use types::{InstrumentQuery, InstrumentType, MarketType};

pub mod blocking {
    pub use super::market_data::MassiveHistoricalMarket;
    pub use super::reference::blocking::MassiveInstrumentCatalog;
}

#[cfg(test)]
mod tests {
    use futures_util::{SinkExt, StreamExt};
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use secrecy::SecretString;

    use super::{
        InstrumentQuery, MarketType, MassiveChannelConfig, MassiveConnection,
        MassiveConnectionConfig,
    };
    use crate::application::capabilities::reference::{
        AsyncInstrumentCatalogConnection, InstrumentCatalogConnection,
    };
    use crate::application::{
        AsyncHistoricalMarketDataConnection, AsyncMarketEventSource,
        HistoricalMarketDataConnection, HistoricalMarketRequest, MarketDataKind,
        MarketSubscription,
    };

    fn assert_async_catalog<T: AsyncInstrumentCatalogConnection>(_value: &T) {}
    fn assert_async_live<T: AsyncMarketEventSource>(_value: &T) {}
    fn assert_async_historical<T: AsyncHistoricalMarketDataConnection>(_value: &T) {}

    #[tokio::test(flavor = "current_thread")]
    async fn catalog_uses_caller_runtime_and_preserves_provider_venue() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).unwrap();
            let body = r#"{"results":[{"ticker":"SPY","primary_exchange":"XNAS","active":true}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let provider = MassiveConnection::connect(MassiveConnectionConfig {
            environment: "test".into(),
            rest_base_url: format!("http://{address}"),
            api_key: SecretString::new("test-key".into()),
        })
        .unwrap();
        let mut catalog = provider.instrument_catalog(InstrumentQuery::equities());
        assert_async_catalog(&catalog);
        let facts = catalog.fetch_instruments().await.unwrap();
        server.join().unwrap();
        assert_eq!(facts.participant.id.as_str(), "massive");
        assert_eq!(facts.instruments[0].source_venue.as_deref(), Some("XNAS"));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn blocking_catalog_rejects_tokio_worker() {
        let provider = MassiveConnection::connect(MassiveConnectionConfig {
            environment: "test".into(),
            rest_base_url: "http://127.0.0.1:1".into(),
            api_key: SecretString::new("test-key".into()),
        })
        .unwrap();
        let mut catalog = provider
            .blocking_instrument_catalog(InstrumentQuery::equities())
            .unwrap();
        let error = catalog.fetch_instruments().unwrap_err();
        assert!(matches!(
            error,
            crate::application::IntegrationError::InvalidRequest(_)
        ));
        let mut historical = provider
            .blocking_historical_market(MarketType::Equity)
            .unwrap();
        let request = HistoricalMarketRequest {
            symbol: kairos_domain_types::Symbol::new("SPY").unwrap(),
            data_kind: MarketDataKind::Bar,
            start_time_unix_nanos: kairos_domain_types::UnixNanos::new(1),
            end_time_unix_nanos: kairos_domain_types::UnixNanos::new(2),
            interval: Some("1m".into()),
            adjusted: Some(false),
        };
        assert!(matches!(
            HistoricalMarketDataConnection::fetch(&mut historical, &request),
            Err(crate::application::IntegrationError::InvalidRequest(_))
        ));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn live_market_awaits_events_on_the_callers_runtime() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let auth = socket.next().await.unwrap().unwrap();
            assert!(auth.to_text().unwrap().contains("\"action\":\"auth\""));
            let subscribe = socket.next().await.unwrap().unwrap();
            assert!(subscribe
                .to_text()
                .unwrap()
                .contains("\"action\":\"subscribe\""));
            socket
                .send(tokio_tungstenite::tungstenite::Message::Text(
                    r#"[{"ev":"Q","sym":"SPY","bp":"500.1","bs":"10","ap":"500.2","as":"11","t":1800000000000}]"#
                        .into(),
                ))
                .await
                .unwrap();
        });
        let provider = MassiveConnection::connect(MassiveConnectionConfig {
            environment: "test".into(),
            rest_base_url: "http://127.0.0.1:1".into(),
            api_key: SecretString::new("test-key".into()),
        })
        .unwrap();
        let mut live = provider
            .live_market(
                MarketType::Equity,
                format!("ws://{address}"),
                MassiveChannelConfig {
                    event_queue_capacity: 8,
                },
            )
            .unwrap();
        assert_async_live(&live);
        live.connect_channel().await.unwrap();
        live.subscribe(MarketSubscription::new(["SPY"]).unwrap())
            .await
            .unwrap();
        let event = live.next_market_event().await.unwrap();
        assert_eq!(event.symbol.as_str(), "SPY");
        live.disconnect_channel().await.unwrap();
        server.await.unwrap();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn historical_market_uses_async_http_on_the_callers_runtime() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).unwrap();
            let body = r#"{"results":[{"t":1800000000000,"o":500.0,"h":501.0,"l":499.0,"c":500.5,"v":1000}]}"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let provider = MassiveConnection::connect(MassiveConnectionConfig {
            environment: "test".into(),
            rest_base_url: format!("http://{address}"),
            api_key: SecretString::new("test-key".into()),
        })
        .unwrap();
        let mut historical = provider.historical_market(MarketType::Equity).unwrap();
        assert_async_historical(&historical);
        let events = historical
            .fetch(&HistoricalMarketRequest {
                symbol: kairos_domain_types::Symbol::new("SPY").unwrap(),
                data_kind: MarketDataKind::Bar,
                start_time_unix_nanos: kairos_domain_types::UnixNanos::new(
                    1_799_000_000_000_000_000,
                ),
                end_time_unix_nanos: kairos_domain_types::UnixNanos::new(1_801_000_000_000_000_000),
                interval: Some("1m".into()),
                adjusted: Some(false),
            })
            .await
            .unwrap();
        server.join().unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].bar.as_ref().unwrap().timeframe, "1m");
    }
}
