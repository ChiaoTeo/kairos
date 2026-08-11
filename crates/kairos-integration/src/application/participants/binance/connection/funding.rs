//! Funding capability handles projected from a Binance principal context.

use super::*;

/// Simple Earn is an operation capability within the Funding connection
/// domain, not a separate financial product family.
pub struct BinanceSimpleEarn {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

/// Internal wallet transfer uses the same authenticated principal context.
pub struct BinanceTransfer {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

impl AsyncEarnConnection for BinanceSimpleEarn {
    async fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, IntegrationError> {
        let mut params = std::collections::BTreeMap::new();
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
            .signed_get_async("/sapi/v1/simple-earn/products", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_product).collect())
            .unwrap_or_default())
    }

    async fn positions(
        &mut self,
        asset: Option<&str>,
    ) -> Result<Vec<EarnPosition>, IntegrationError> {
        let params = asset
            .map(|asset| std::collections::BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get_async("/sapi/v1/simple-earn/positions", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_position).collect())
            .unwrap_or_default())
    }

    async fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, IntegrationError> {
        let params = asset
            .map(|asset| std::collections::BTreeMap::from([("asset".into(), asset.into())]))
            .unwrap_or_default();
        let payload = self
            .client
            .signed_get_async("/sapi/v1/simple-earn/rewardsRecord", params)
            .await
            .map_err(map_exchange_error)?;
        Ok(payload
            .get("rows")
            .and_then(serde_json::Value::as_array)
            .map(|rows| rows.iter().filter_map(earn::normalize_reward).collect())
            .unwrap_or_default())
    }

    async fn subscribe(
        &mut self,
        request: &EarnSubscribeRequest,
    ) -> CommandResult<EarnActionResult> {
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/subscribe",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/subscribe",
        };
        let mut params = std::collections::BTreeMap::from([
            (String::from("productId"), request.product_id.clone()),
            (String::from("amount"), request.amount.to_string()),
        ]);
        if let Some(auto_renew) = request.auto_renew {
            params.insert("autoSubscribe".into(), auto_renew.to_string());
        }
        let payload = match self.client.signed_post_async(path, params).await {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }

    async fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnActionResult> {
        let path = match request.product_type {
            EarnProductType::Locked => "/sapi/v1/simple-earn/locked/redeem",
            EarnProductType::Flexible => "/sapi/v1/simple-earn/flexible/redeem",
        };
        let mut params = std::collections::BTreeMap::from([(
            String::from("productId"),
            request.product_id.clone(),
        )]);
        if let Some(amount) = &request.amount {
            params.insert("amount".into(), amount.to_string());
        }
        if let Some(destination) = &request.destination_account {
            params.insert("destAccount".into(), destination.clone());
        }
        let payload = match self.client.signed_post_async(path, params).await {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(earn::normalize_action(&payload))
    }
}

impl AsyncTransferConnection for BinanceTransfer {
    async fn transfer(&mut self, request: &TransferRequest) -> CommandResult<TransferResult> {
        let params = transfer::transfer_params(request)?;
        let payload = match self
            .client
            .signed_post_async("/sapi/v1/asset/transfer", params)
            .await
        {
            Ok(payload) => payload,
            Err(error) => return crate::services::transport::http::command_error_outcome(error),
        };
        Ok(transfer::normalize_transfer(&payload))
    }
}
