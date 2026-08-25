websocket_connection!(BinanceUsdMWebSocketConnection, "usdm.websocket", "usdm");
market_websocket_capabilities!(BinanceUsdMWebSocketConnection, "usdm");

#[cfg(test)]
mod tests {
    use super::BinanceUsdMWebSocketConnection;
    use crate::participants::binance::BinanceWebSocketConfig;
    use crate::services::participants::binance::market_stream::{StreamRoute, StreamShard};
    use crate::{ConnectionKey, IntegrationError};

    fn config(endpoint: &str) -> BinanceWebSocketConfig {
        BinanceWebSocketConfig {
            environment: "public".into(),
            endpoint: endpoint.into(),
            credential: None,
            event_capacity: 32,
        }
    }

    #[test]
    fn creates_separate_current_public_and_market_routes() {
        let connection = BinanceUsdMWebSocketConnection::new(
            ConnectionKey::new("usdm-test").unwrap(),
            config("wss://fstream.binance.com"),
        )
        .unwrap();

        assert_eq!(
            connection.services[&StreamShard {
                route: StreamRoute::Public,
                index: 0,
            }]
                .endpoint(),
            "wss://fstream.binance.com/public/ws"
        );
        assert_eq!(
            connection.services[&StreamShard {
                route: StreamRoute::Market,
                index: 0,
            }]
                .endpoint(),
            "wss://fstream.binance.com/market/ws"
        );
    }

    #[test]
    fn rejects_the_decommissioned_legacy_endpoint() {
        assert!(matches!(
            BinanceUsdMWebSocketConnection::new(
                ConnectionKey::new("usdm-legacy").unwrap(),
                config("wss://fstream.binance.com/ws"),
            ),
            Err(IntegrationError::InvalidRequest(_))
        ));
    }
}

user_websocket_connection!(
    BinanceUsdMUserWebSocketConnection,
    "usdm.user.websocket",
    "/fapi/v1/listenKey"
);
