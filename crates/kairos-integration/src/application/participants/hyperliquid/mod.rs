//! Hyperliquid exchange-native public connection facade.

mod config;
mod connection;
mod market;
mod reference;

pub use config::HyperliquidConnectionConfig;
pub use connection::HyperliquidConnection;
pub use market::{HyperliquidLiveMarket, HyperliquidMarketSnapshot};
pub use reference::{HyperliquidInstrumentCatalog, HyperliquidInstrumentProduct};

pub mod blocking {
    pub use super::reference::blocking::HyperliquidInstrumentCatalog;
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::{HyperliquidConnection, HyperliquidConnectionConfig};
    use crate::application::capabilities::reference::{
        AsyncInstrumentCatalogConnection, InstrumentCatalogConnection,
    };
    use crate::application::AsyncMarketSnapshotConnection;
    use kairos_domain_types::ProviderSymbol;

    #[tokio::test(flavor = "current_thread")]
    async fn catalog_uses_caller_runtime_and_trait_proves_capability() {
        fn assert_catalog<T: AsyncInstrumentCatalogConnection>(_value: &T) {}
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let length = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..length]);
            assert!(request.contains("metaAndAssetCtxs"));
            let body = r#"[{"universe":[{"name":"BTC","szDecimals":5}]},[{"markPx":"50000"}]]"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "test".into(),
            info_endpoint: format!("http://{address}/info"),
        })
        .unwrap();
        let mut catalog = provider.instrument_catalog();
        assert_catalog(&catalog);
        let facts = catalog.fetch_instruments().await.unwrap();
        server.join().unwrap();
        assert_eq!(facts.instruments[0].source_symbol.as_str(), "BTC");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn spot_catalog_uses_the_provider_native_spot_request() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let length = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..length]).contains("spotMetaAndAssetCtxs"));
            let body = r#"[{"tokens":[{"name":"USDC","szDecimals":8,"index":0},{"name":"PURR","szDecimals":0,"index":1}],"universe":[{"name":"PURR/USDC","tokens":[1,0],"index":0}]},[{"midPx":"0.1"}]]"#;
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
        });
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "test".into(),
            info_endpoint: format!("http://{address}/info"),
        })
        .unwrap();
        let mut catalog = provider.spot_instrument_catalog();
        let facts = catalog.fetch_instruments().await.unwrap();
        server.join().unwrap();
        assert_eq!(facts.instruments[0].source_symbol.as_str(), "PURR/USDC");
        assert_eq!(
            facts.instruments[0].kind,
            crate::application::capabilities::reference::ExternalInstrumentKind::Spot
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn blocking_catalog_rejects_tokio_worker() {
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "test".into(),
            info_endpoint: "http://127.0.0.1:1/info".into(),
        })
        .unwrap();
        let mut catalog = provider.blocking_instrument_catalog().unwrap();
        assert!(catalog.fetch_instruments().is_err());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn market_snapshot_normalizes_all_mids() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let length = stream.read(&mut request).unwrap();
            assert!(String::from_utf8_lossy(&request[..length]).contains("allMids"));
            let body = r#"{"BTC":"50000.5","ETH":"3000"}"#;
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
        });
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "test".into(),
            info_endpoint: format!("http://{address}/info"),
        })
        .unwrap();
        let mut snapshot = provider.market_snapshot();
        let events = snapshot
            .fetch_snapshot(&[ProviderSymbol::new("BTC").unwrap()])
            .await
            .unwrap();
        server.join().unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].price.unwrap().to_string(), "50000.5");
    }
}
