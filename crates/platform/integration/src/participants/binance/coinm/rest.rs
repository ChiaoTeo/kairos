rest_connection!(BinanceCoinMRestConnection, "coinm.rest");
futures_rest_capabilities!(
    BinanceCoinMRestConnection,
    "/dapi/v1",
    crate::ExternalInstrumentKind::Future
);
