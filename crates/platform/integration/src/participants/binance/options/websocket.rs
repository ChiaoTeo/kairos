websocket_connection!(BinanceOptionsWebSocketConnection, "options.websocket");
market_websocket_capabilities!(BinanceOptionsWebSocketConnection, "options");

user_websocket_connection!(
    BinanceOptionsUserWebSocketConnection,
    "options.user.websocket",
    "/eapi/v1/listenKey"
);
