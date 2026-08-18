websocket_api_connection!(BinanceUsdMWebSocketApiConnection, "usdm.websocket-api");

impl crate::AccountQuery for BinanceUsdMWebSocketApiConnection {
    async fn fetch_account(
        &mut self,
        segment: &crate::ExternalAccountSegment,
    ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
        let value = self
            .service
            .request("v2/account.status", Vec::new())
            .await?
            .into_query_result()?;
        crate::services::participants::binance::account::futures(segment, &value, "perpetual")
    }
}
