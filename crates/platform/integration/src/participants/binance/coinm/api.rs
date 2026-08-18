websocket_api_connection!(BinanceCoinMWebSocketApiConnection, "coinm.websocket-api");

impl crate::AccountQuery for BinanceCoinMWebSocketApiConnection {
    async fn fetch_account(
        &mut self,
        segment: &crate::ExternalAccountSegment,
    ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
        let value = self
            .service
            .request("account.status", Vec::new())
            .await?
            .into_query_result()?;
        crate::services::participants::binance::account::futures(segment, &value, "future")
    }
}
