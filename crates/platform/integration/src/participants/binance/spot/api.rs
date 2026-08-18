websocket_api_connection!(BinanceSpotWebSocketApiConnection, "spot.websocket-api");

impl crate::AccountQuery for BinanceSpotWebSocketApiConnection {
    async fn fetch_account(
        &mut self,
        segment: &crate::ExternalAccountSegment,
    ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
        let value = self
            .service
            .request("account.status", Vec::new())
            .await?
            .into_query_result()?;
        crate::services::participants::binance::account::spot(segment, &value)
    }
}

impl crate::OrderQuery for BinanceSpotWebSocketApiConnection {
    async fn open_orders(
        &mut self,
        query: &crate::ExternalOrderQuery,
    ) -> Result<Vec<crate::ExternalOrder>, crate::IntegrationError> {
        let params = order_query_params(query, false)?;
        let value = self
            .service
            .request("openOrders.status", params)
            .await?
            .into_query_result()?;
        crate::services::participants::binance::execution::orders(
            &self.descriptor().binding_id,
            &value,
        )
    }

    async fn order_history(
        &mut self,
        query: &crate::ExternalOrderQuery,
    ) -> Result<Vec<crate::ExternalOrder>, crate::IntegrationError> {
        let params = order_query_params(query, false)?;
        let value = self
            .service
            .request("allOrders", params)
            .await?
            .into_query_result()?;
        crate::services::participants::binance::execution::orders(
            &self.descriptor().binding_id,
            &value,
        )
    }

    async fn order_detail(
        &mut self,
        query: &crate::ExternalOrderQuery,
    ) -> Result<Option<crate::ExternalOrder>, crate::IntegrationError> {
        let params = order_query_params(query, true)?;
        let value = self
            .service
            .request("order.status", params)
            .await?
            .into_query_result()?;
        Ok(crate::services::participants::binance::execution::orders(
            &self.descriptor().binding_id,
            &value,
        )?
        .into_iter()
        .next())
    }
}

fn order_query_params(
    query: &crate::ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, crate::IntegrationError> {
    let symbol = query.symbol.as_ref().ok_or_else(|| {
        crate::IntegrationError::InvalidRequest(
            "Binance Spot WebSocket order query requires symbol".into(),
        )
    })?;
    let mut params = vec![("symbol", symbol.to_string())];
    if detail {
        let order_id = query.order_id.as_ref().ok_or_else(|| {
            crate::IntegrationError::InvalidRequest(
                "Binance Spot WebSocket order detail requires order id".into(),
            )
        })?;
        params.push(("origClientOrderId", order_id.to_string()));
    }
    if let Some(limit) = query.limit {
        params.push(("limit", limit.to_string()));
    }
    Ok(params)
}
