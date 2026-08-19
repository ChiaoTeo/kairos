use serde_json::{Value, json};

use super::{
    HyperliquidFillRecord, HyperliquidFundingRecord, HyperliquidHistoryPage,
    HyperliquidHistoryQuery, HyperliquidLedgerRecord, history,
};
use crate::participants::hyperliquid::HyperliquidAccountRestConfig;
use crate::services::participants::hyperliquid::account;
use crate::services::participants::hyperliquid::rest::RestService;
use crate::{
    AccountQuery, ConnectionDescriptor, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalOrder, ExternalOrderQuery, IntegrationError, OrderQuery,
};

pub struct HyperliquidAccountRestConnection {
    service: RestService,
    address: String,
}

impl HyperliquidAccountRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: HyperliquidAccountRestConfig,
    ) -> Result<Self, IntegrationError> {
        if !config.address.starts_with("0x") || config.address.len() != 42 {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid account address must be a 42-character hexadecimal address".into(),
            ));
        }
        let address = config.address.to_ascii_lowercase();
        Ok(Self {
            service: RestService::new(
                connection_key,
                config.connection,
                "account.rest",
                Some(address.clone()),
            )?,
            address,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    async fn info(&mut self, body: Value) -> Result<Value, IntegrationError> {
        let endpoint = self.service.endpoint().to_owned();
        self.service
            .client()
            .post_query_json_with_headers(&endpoint, &[], &body)
            .await
            .map_err(|error| IntegrationError::Transport(error.to_string()))
    }

    pub async fn fetch_user_fills(
        &mut self,
        query: &HyperliquidHistoryQuery,
    ) -> Result<HyperliquidHistoryPage<HyperliquidFillRecord>, IntegrationError> {
        query.validate()?;
        let mut body = json!({
            "type": "userFillsByTime",
            "user": self.address,
            "startTime": query.start_millis(),
            "aggregateByTime": query.aggregate_fills_by_time,
        });
        if let Some(end) = query.end_millis() {
            body["endTime"] = json!(end);
        }
        history::fills(&self.info(body).await?)
    }

    pub async fn fetch_user_funding(
        &mut self,
        query: &HyperliquidHistoryQuery,
    ) -> Result<HyperliquidHistoryPage<HyperliquidFundingRecord>, IntegrationError> {
        query.validate()?;
        let body = history_body("userFunding", &self.address, query);
        history::funding(&self.info(body).await?)
    }

    pub async fn fetch_non_funding_ledger(
        &mut self,
        query: &HyperliquidHistoryQuery,
    ) -> Result<HyperliquidHistoryPage<HyperliquidLedgerRecord>, IntegrationError> {
        query.validate()?;
        let body = history_body("userNonFundingLedgerUpdates", &self.address, query);
        history::ledger(&self.info(body).await?)
    }
}

fn history_body(kind: &str, address: &str, query: &HyperliquidHistoryQuery) -> Value {
    let mut body = json!({
        "type": kind,
        "user": address,
        "startTime": query.start_millis(),
    });
    if let Some(end) = query.end_millis() {
        body["endTime"] = json!(end);
    }
    body
}

impl AccountQuery for HyperliquidAccountRestConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        let address = self.address.clone();
        let perpetual = self
            .info(json!({"type": "clearinghouseState", "user": address}))
            .await?;
        let address = self.address.clone();
        let spot = self
            .info(json!({"type": "spotClearinghouseState", "user": address}))
            .await?;
        account::snapshot(segment, &perpetual, &spot)
    }
}

impl OrderQuery for HyperliquidAccountRestConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let address = self.address.clone();
        let connection_key = self.descriptor().connection_key.clone();
        let value = self
            .info(json!({"type": "openOrders", "user": address}))
            .await?;
        account::orders(&connection_key, &value, query)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let address = self.address.clone();
        let connection_key = self.descriptor().connection_key.clone();
        let value = self
            .info(json!({"type": "historicalOrders", "user": address}))
            .await?;
        account::orders(&connection_key, &value, query)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        Ok(self.order_history(query).await?.into_iter().find(|order| {
            query
                .order_id
                .as_ref()
                .is_none_or(|id| &order.order_id == id)
        }))
    }
}
