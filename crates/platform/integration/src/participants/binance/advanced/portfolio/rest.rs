use crate::services::participants::binance::{account, execution};
use crate::{
    AccountQuery, CommandResult, ExternalAccountSegment, ExternalAccountSnapshot, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderCommand, OrderEntryEvent, OrderEntryRequest,
    OrderQuery,
};

rest_connection!(
    BinancePortfolioMarginRestConnection,
    "advanced.portfolio.rest"
);

impl AccountQuery for BinancePortfolioMarginRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let value = self.service.signed_get("/papi/v1/balance", &[]).await?;
        account::portfolio(segment, &value)
    }
}

impl OrderCommand for BinancePortfolioMarginRestConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        let path = format!(
            "/papi/v1/{}/order",
            family(
                request
                    .participant_instrument
                    .instrument_type
                    .as_ref()
                    .map(|value| value.as_str())
            )
        );
        let params = execution::params(request)?;
        let outcome = self.service.signed_post_command(&path, &params).await?;
        execution::submitted_outcome(request, outcome)
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let path = format!(
            "/papi/v1/{}/order",
            family(
                request
                    .participant_instrument
                    .instrument_type
                    .as_ref()
                    .map(|value| value.as_str())
            )
        );
        let params = [
            (
                "symbol",
                request.participant_instrument.source_symbol.as_str().into(),
            ),
            ("orderId", remote_order_id.into()),
        ];
        let outcome = self.service.signed_delete_command(&path, &params).await?;
        execution::canceled_outcome(request, remote_order_id, at_unix_nanos, outcome)
    }
}

impl OrderQuery for BinancePortfolioMarginRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.orders(query, "openOrders", false).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.orders(query, "allOrders", false).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        Ok(self.orders(query, "order", true).await?.into_iter().next())
    }
}

impl BinancePortfolioMarginRestConnection {
    async fn orders(
        &mut self,
        query: &ExternalOrderQuery,
        operation: &str,
        detail: bool,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let family = family(query.instrument_type.as_ref().map(|value| value.as_str()));
        let path = format!("/papi/v1/{family}/{operation}");
        let mut params = Vec::new();
        if let Some(symbol) = &query.symbol {
            params.push(("symbol", symbol.to_string()));
        } else if detail || family == "margin" {
            return Err(IntegrationError::InvalidRequest(
                "Binance Portfolio Margin order query requires symbol".into(),
            ));
        }
        if detail {
            let order_id = query.order_id.as_ref().ok_or_else(|| {
                IntegrationError::InvalidRequest(
                    "Binance Portfolio Margin order detail requires order id".into(),
                )
            })?;
            params.push(("origClientOrderId", order_id.to_string()));
        }
        if let Some(limit) = query.limit {
            params.push(("limit", limit.to_string()));
        }
        let binding_id = self.descriptor().binding_id.clone();
        let value = self.service.signed_get(&path, &params).await?;
        execution::orders(&binding_id, &value)
    }
}

fn family(instrument_type: Option<&str>) -> &'static str {
    let instrument_type = instrument_type.unwrap_or_default().to_ascii_lowercase();
    if instrument_type.contains("coin") || instrument_type == "cm" {
        "cm"
    } else if instrument_type.contains("margin") || instrument_type == "spot" {
        "margin"
    } else {
        "um"
    }
}
