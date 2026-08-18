rest_connection!(
    BinancePortfolioMarginProRestConnection,
    "advanced.portfolio.pro.rest"
);

impl crate::AccountQuery for BinancePortfolioMarginProRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &crate::ExternalAccountSegment,
    ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/portfolio/balance", &[])
            .await?;
        crate::services::participants::binance::account::portfolio(segment, &value)
    }
}
