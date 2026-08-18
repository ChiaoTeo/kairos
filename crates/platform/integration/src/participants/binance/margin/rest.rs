use crate::services::participants::binance::{account, execution};
use crate::{
    AccountQuery, CommandResult, ExternalAccountSegment, ExternalAccountSnapshot, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderCommand, OrderEntryEvent, OrderEntryRequest,
    OrderQuery,
};

rest_connection!(BinanceMarginRestConnection, "margin.rest");

impl AccountQuery for BinanceMarginRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/margin/account", &[])
            .await?;
        account::margin(segment, &value)
    }
}

impl OrderCommand for BinanceMarginRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let params = execution::params(request)?;
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/margin/order", &params)
            .await?;
        execution::submitted_outcome(request, outcome)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let params = [
            (
                "symbol",
                request.participant_instrument.source_symbol.as_str().into(),
            ),
            ("orderId", remote_order_id.into()),
        ];
        let outcome = self
            .service
            .signed_delete_command("/sapi/v1/margin/order", &params)
            .await?;
        execution::canceled_outcome(request, remote_order_id, at_unix_nanos, outcome)
    }
}

impl OrderQuery for BinanceMarginRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/openOrders", &params)
            .await?;
        execution::orders(&self.descriptor().binding_id, &value)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/allOrders", &params)
            .await?;
        execution::orders(&self.descriptor().binding_id, &value)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = query_params(query, true)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/order", &params)
            .await?;
        Ok(execution::orders(&self.descriptor().binding_id, &value)?
            .into_iter()
            .next())
    }
}

fn query_params(
    query: &ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let symbol = query.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest("Binance Margin order query requires symbol".into())
    })?;
    let mut values = vec![("symbol", symbol.to_string())];
    if detail {
        let order_id = query.order_id.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest("Binance Margin order detail requires order id".into())
        })?;
        values.push(("origClientOrderId", order_id.to_string()));
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}
