//! Binance Alpha Trading event-stream connection.

websocket_connection!(
    BinanceAlphaTradingWebSocketConnection,
    "advanced.alpha.websocket",
    "alpha"
);
market_websocket_capabilities!(BinanceAlphaTradingWebSocketConnection, "alpha");
