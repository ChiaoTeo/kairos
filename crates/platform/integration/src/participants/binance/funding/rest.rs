use crate::services::participants::binance::account;
use crate::{AccountQuery, ExternalAccountSegment, ExternalAccountSnapshot, IntegrationError};

rest_connection!(BinanceFundingRestConnection, "funding.rest");

impl AccountQuery for BinanceFundingRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let value = self
            .service
            .signed_post_query("/sapi/v1/asset/get-funding-asset", &[])
            .await?;
        account::funding(segment, &value)
    }
}
