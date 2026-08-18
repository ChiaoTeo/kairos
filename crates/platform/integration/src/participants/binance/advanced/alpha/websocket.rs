//! Binance Alpha Trading event-stream connection.

websocket_connection!(
    BinanceAlphaTradingWebSocketConnection,
    "advanced.alpha.websocket"
);
market_websocket_capabilities!(BinanceAlphaTradingWebSocketConnection, "alpha");
