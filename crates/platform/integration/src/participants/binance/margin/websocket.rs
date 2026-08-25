websocket_connection!(
    BinanceMarginWebSocketConnection,
    "margin.websocket",
    "margin"
);
market_websocket_capabilities!(BinanceMarginWebSocketConnection, "margin");

user_websocket_connection!(
    BinanceMarginUserWebSocketConnection,
    "margin.user.websocket",
    "/sapi/v1/userDataStream"
);
