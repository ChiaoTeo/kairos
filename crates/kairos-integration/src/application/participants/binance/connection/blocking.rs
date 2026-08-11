//! Explicit synchronous compatibility projections for Binance capabilities.

use std::collections::BTreeMap;

use crate::application::capabilities::account_facts::{
    ExternalAccountSegment, ExternalAccountSnapshot,
};
use crate::application::capabilities::reference::{
    ExternalInstrumentCatalog, InstrumentCatalogConnection,
};
use crate::application::capabilities::{ConnectionHealth, OrderEntryEvent, OrderEntryRequest};
use crate::application::{
    CommandResult, EarnActionResult, EarnPosition, EarnProduct, EarnProductType, EarnRedeemRequest,
    EarnReward, EarnSubscribeRequest, ExternalAccountCredentialProfile, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery, IntegrationError,
    OrderEntryConnection, OrderEventSource, OrderQueryConnection, TransferRequest, TransferResult,
};
use crate::services::participants::binance::funding::{account as funding, earn, transfer};
use crate::services::participants::binance::order_query::BinanceOrderQueryConnection;
use crate::services::participants::binance::spot::account::BinanceSpotAccountClient;
use crate::services::participants::binance::spot::order::BinanceSpotOrderConnection;
use crate::services::participants::binance::spot::order_events::BinanceSpotOrderEventSource;
use crate::services::transport::http::command_error_outcome;

use super::{
    instrument_catalog, map_exchange_error, BinanceSpotProviderRuntime, ConnectionDescriptor,
    InstrumentType, RequestPriority,
};

pub struct BinanceSpotOrderEntry {
    pub(super) inner: BinanceSpotOrderConnection,
}

pub struct BinanceSpotOrderQuery {
    pub(super) inner: BinanceOrderQueryConnection,
}

pub struct BinanceSpotOrderEvents {
    pub(super) inner: BinanceSpotOrderEventSource,
}

pub struct BinanceFundingAccountRead {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceFundingCredentialInspection {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceSimpleEarn {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceTransfer {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) instrument_type: InstrumentType,
    pub(super) base_url: String,
    pub(super) runtime: BinanceSpotProviderRuntime,
}

fn reject_async_runtime() -> Result<(), IntegrationError> {
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(IntegrationError::InvalidRequest(
            "kairos_integration::blocking cannot run on a Tokio runtime worker; use the default async capability or move the blocking call to a dedicated thread".into(),
        ));
    }
    Ok(())
}

impl OrderEntryConnection for BinanceSpotOrderEntry {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent> {
        reject_async_runtime()?;
        self.inner.submit_order(request)
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        reject_async_runtime()?;
        self.inner
            .cancel_order(request, remote_order_id, at_unix_nanos)
    }
}

impl OrderQueryConnection for BinanceSpotOrderQuery {
    fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        self.inner.open_orders(query)
    }

    fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        self.inner.order_history(query)
    }

    fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        reject_async_runtime()?;
        self.inner.order_detail(query)
    }
}

impl OrderEventSource for BinanceSpotOrderEvents {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        self.inner.connect_channel()
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        self.inner.disconnect_channel()
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        reject_async_runtime()?;
        self.inner.reconnect_channel()
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.inner.channel_health()
    }

    fn try_next_order_event(
        &mut self,
    ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError> {
        reject_async_runtime()?;
        self.inner.try_next_order_event()
    }
}

impl BinanceFundingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        reject_async_runtime()?;
        let payload = self
            .client
            .signed_post_query("/sapi/v1/asset/get-funding-asset", BTreeMap::new())
            .map_err(super::map_exchange_error)?;
        funding::normalize_funding(segment, &payload).map_err(IntegrationError::InvalidPayload)
    }
}

impl BinanceFundingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        reject_async_runtime()?;
        let payload = self.client.account().map_err(super::map_exchange_error)?;
        Ok(funding::normalize_credential_profile(&payload))
    }
}

impl BinanceSimpleEarn {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, IntegrationError> {
        reject_async_runtime()?;
        let mut params = BTreeMap::new();
        if let Some(asset) = asset {
            params.insert("asset".into(), asset.into());
        }
        if let Some(product_type) = product_type {
            params.insert(
                "productType".into(),
                earn::product_type_name(product_type).to_ascii_uppercase(),
            );
        }
        let payload = self
            .client
            .signed_get("/sapi/v1/simple-earn/products", params)
            .map_err(super::map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_product).collect())
            .unwrap_or_default())
    }

    pub fn positions(
        &mut self,
        asset: Option<&str>,
    ) -> Result<Vec<EarnPosition>, IntegrationError> {
        reject_async_runtime()?;
        let params = asset
            .map(|asset| BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get("/sapi/v1/simple-earn/positions", params)
            .map_err(super::map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_position).collect())
            .unwrap_or_default())
    }

    pub fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, IntegrationError> {
        reject_async_runtime()?;
        let params = asset
            .map(|asset| BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get("/sapi/v1/simple-earn/rewardsRecord", params)
            .map_err(super::map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_reward).collect())
            .unwrap_or_default())
    }

    pub fn subscribe(&mut self, request: &EarnSubscribeRequest) -> CommandResult<EarnActionResult> {
        reject_async_runtime()?;
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/subscribe",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/subscribe",
        };
        let mut params = BTreeMap::from([
            (String::from("productId"), request.product_id.clone()),
            (String::from("amount"), request.amount.to_string()),
        ]);
        if let Some(auto_renew) = request.auto_renew {
            params.insert("autoSubscribe".into(), auto_renew.to_string());
        }
        let payload = match self.client.signed_post(path, params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }

    pub fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnActionResult> {
        reject_async_runtime()?;
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/redeem",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/redeem",
        };
        let mut params = BTreeMap::from([(String::from("productId"), request.product_id.clone())]);
        if let Some(amount) = &request.amount {
            params.insert("amount".into(), amount.to_string());
        }
        if let Some(destination) = &request.destination_account {
            params.insert("destAccount".into(), destination.clone());
        }
        let payload = match self.client.signed_post(path, params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }
}

impl BinanceTransfer {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn transfer(&mut self, request: &TransferRequest) -> CommandResult<TransferResult> {
        reject_async_runtime()?;
        let params = transfer::transfer_params(request)?;
        let payload = match self.client.signed_post("/sapi/v1/asset/transfer", params) {
            Ok(payload) => payload,
            Err(error) => return command_error_outcome(error),
        };
        Ok(transfer::normalize_transfer(&payload))
    }
}

impl BinanceInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl InstrumentCatalogConnection for BinanceInstrumentCatalog {
    fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        reject_async_runtime()?;
        self.runtime
            .acquire(1, RequestPriority::Background)
            .map_err(map_exchange_error)?;
        let response = self
            .runtime
            .http()
            .get_json_response_with_headers_and_query(
                &format!("{}{}", self.base_url, self.instrument_type.path()),
                &[],
                &[],
            )
            .map_err(map_exchange_error)?;
        self.runtime.observe_response(&response);
        match self.instrument_type {
            InstrumentType::Spot => instrument_catalog::normalize_spot(&response.body),
            InstrumentType::UsdMFutures | InstrumentType::CoinMFutures => {
                instrument_catalog::normalize_derivatives(&response.body)
            }
            InstrumentType::Option => instrument_catalog::normalize_options(&response.body),
        }
    }
}

#[cfg(test)]
mod tests {
    #[tokio::test]
    async fn blocking_facade_rejects_tokio_runtime_workers() {
        let error = super::reject_async_runtime().unwrap_err();
        assert!(matches!(
            error,
            crate::application::IntegrationError::InvalidRequest(_)
        ));
    }
}
