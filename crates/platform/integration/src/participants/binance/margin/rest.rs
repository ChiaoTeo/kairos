use super::super::{BinanceCancelAllScope, BinanceHistoryQuery, BinanceTradeRecord, history};
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

impl BinanceMarginRestConnection {
    pub async fn cancel_all_open_orders(
        &mut self,
        scope: &BinanceCancelAllScope,
    ) -> CommandResult<BinanceCancelAllScope> {
        match self
            .service
            .signed_delete_command(
                "/sapi/v1/margin/openOrders",
                &[("symbol", scope.symbol.to_string())],
            )
            .await?
        {
            crate::CommandOutcome::Confirmed(_) => {
                Ok(crate::CommandOutcome::Confirmed(scope.clone()))
            },
            crate::CommandOutcome::Rejected(error) => Ok(crate::CommandOutcome::Rejected(error)),
            crate::CommandOutcome::Indeterminate(error) => {
                Ok(crate::CommandOutcome::Indeterminate(error))
            },
        }
    }

    pub async fn fetch_account_trades(
        &mut self,
        query: &BinanceHistoryQuery,
    ) -> Result<Vec<BinanceTradeRecord>, IntegrationError> {
        let payload = self
            .service
            .signed_get("/sapi/v1/margin/myTrades", &query.params(true, false)?)
            .await?;
        history::trades(&payload)
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
        let params = query_params(query, false, false)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/openOrders", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, &value)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let params = query_params(query, false, true)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/allOrders", &params)
            .await?;
        execution::orders(&self.descriptor().connection_key, &value)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let params = query_params(query, true, true)?;
        let value = self
            .service
            .signed_get("/sapi/v1/margin/order", &params)
            .await?;
        Ok(
            execution::orders(&self.descriptor().connection_key, &value)?
                .into_iter()
                .next(),
        )
    }
}

fn query_params(
    query: &ExternalOrderQuery,
    detail: bool,
    symbol_required: bool,
) -> Result<Vec<(&'static str, String)>, IntegrationError> {
    let mut values = Vec::new();
    if let Some(symbol) = &query.symbol {
        values.push(("symbol", symbol.to_string()));
    } else if symbol_required {
        return Err(IntegrationError::InvalidRequest(
            "Binance Margin order query requires symbol".into(),
        ));
    }
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
