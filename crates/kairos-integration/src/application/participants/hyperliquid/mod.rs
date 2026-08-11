//! Hyperliquid exchange-native public connection facade.

mod config;
mod connection;
mod reference;

pub use config::HyperliquidConnectionConfig;
pub use connection::HyperliquidConnection;
pub use reference::HyperliquidInstrumentCatalog;

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
    async fn blocking_catalog_rejects_tokio_worker() {
        let provider = HyperliquidConnection::connect(HyperliquidConnectionConfig {
            environment: "test".into(),
            info_endpoint: "http://127.0.0.1:1/info".into(),
        })
        .unwrap();
        let mut catalog = provider.blocking_instrument_catalog().unwrap();
        assert!(catalog.fetch_instruments().is_err());
    }
}
