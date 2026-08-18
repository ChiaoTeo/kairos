websocket_connection!(BinanceUsdMWebSocketConnection, "usdm.websocket");
market_websocket_capabilities!(BinanceUsdMWebSocketConnection, "usdm");

user_websocket_connection!(
    BinanceUsdMUserWebSocketConnection,
    "usdm.user.websocket",
    "/fapi/v1/listenKey"
);
