websocket_connection!(BinanceCoinMWebSocketConnection, "coinm.websocket", "coinm");
market_websocket_capabilities!(BinanceCoinMWebSocketConnection, "coinm");

user_websocket_connection!(
    BinanceCoinMUserWebSocketConnection,
    "coinm.user.websocket",
    "/dapi/v1/listenKey"
);
