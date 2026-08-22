rest_connection!(BinanceUsdMRestConnection, "usdm.rest");

impl BinanceUsdMRestConnection {
    pub async fn fetch_position_mode(
        &mut self,
    ) -> Result<crate::ExternalPositionMode, crate::IntegrationError> {
        let value = self
            .service
            .signed_get("/fapi/v1/positionSide/dual", &[])
            .await?;
        crate::services::participants::binance::account::position_mode(&value)
    }
}

impl crate::FeeQuery for BinanceUsdMRestConnection {
    async fn fetch_fee_schedule(
        &mut self,
        request: &crate::ExternalFeeScheduleRequest,
    ) -> Result<crate::ExternalFeeSchedule, crate::IntegrationError> {
        let value = self
            .service
            .signed_get(
                "/fapi/v1/commissionRate",
                &[("symbol", request.symbol.to_string())],
            )
            .await?;
        crate::participants::binance::fees::futures(&value)
    }
}
futures_rest_capabilities!(
    BinanceUsdMRestConnection,
    "/fapi/v1",
    "/fapi/v3/account",
    "/fapi/v3/balance",
    crate::ExternalInstrumentKind::Perpetual
);
futures_native_order_extensions!(BinanceUsdMRestConnection, "/fapi/v1");
