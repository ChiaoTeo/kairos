rest_connection!(BinanceCoinMRestConnection, "coinm.rest");
futures_rest_capabilities!(
    BinanceCoinMRestConnection,
    "/dapi/v1",
    crate::ExternalInstrumentKind::Future
);
futures_native_order_extensions!(BinanceCoinMRestConnection, "/dapi/v1");
