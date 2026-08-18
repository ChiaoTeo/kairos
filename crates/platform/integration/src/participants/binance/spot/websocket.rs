websocket_connection!(BinanceSpotWebSocketConnection, "spot.websocket");
market_websocket_capabilities!(BinanceSpotWebSocketConnection, "spot");

user_websocket_connection!(
    BinanceSpotUserWebSocketConnection,
    "spot.user.websocket",
    "/api/v3/userDataStream"
);
