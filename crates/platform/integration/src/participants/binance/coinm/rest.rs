rest_connection!(BinanceCoinMRestConnection, "coinm.rest");

impl crate::FeeQuery for BinanceCoinMRestConnection {
    async fn fetch_fee_schedule(
        &mut self,
        request: &crate::ExternalFeeScheduleRequest,
    ) -> Result<crate::ExternalFeeSchedule, crate::IntegrationError> {
        let value = self
            .service
            .signed_get(
                "/dapi/v1/commissionRate",
                &[("symbol", request.symbol.to_string())],
            )
            .await?;
        crate::participants::binance::fees::futures(&value)
    }
}
futures_rest_capabilities!(
    BinanceCoinMRestConnection,
    "/dapi/v1",
    "/dapi/v1/account",
    "/dapi/v1/balance",
    crate::ExternalInstrumentKind::Future
);
futures_native_order_extensions!(BinanceCoinMRestConnection, "/dapi/v1");
