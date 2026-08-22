rest_connection!(
    BinancePortfolioMarginProRestConnection,
    "advanced.portfolio.pro.rest"
);

impl crate::AccountProfileQuery for BinancePortfolioMarginProRestConnection {
    async fn fetch_account_profile(
        &mut self,
    ) -> Result<crate::ExternalAccountProfile, crate::IntegrationError> {
        self.service
            .signed_get("/sapi/v1/portfolio/account", &[])
            .await?;
        Ok(crate::ExternalAccountProfile {
            account_model: crate::ExternalAccountModel::PortfolioMargin,
            provider_account_model: Some("portfolio_margin_pro".into()),
        })
    }
}

impl crate::AccountQuery for BinancePortfolioMarginProRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &crate::ExternalAccountSegment,
    ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/portfolio/balance", &[])
            .await?;
        crate::services::participants::binance::account::portfolio_pro(segment, &value)
    }
}
