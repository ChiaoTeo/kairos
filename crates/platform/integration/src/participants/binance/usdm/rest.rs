rest_connection!(BinanceUsdMRestConnection, "usdm.rest");
futures_rest_capabilities!(
    BinanceUsdMRestConnection,
    "/fapi/v1",
    crate::ExternalInstrumentKind::Perpetual
);
futures_native_order_extensions!(BinanceUsdMRestConnection, "/fapi/v1");
