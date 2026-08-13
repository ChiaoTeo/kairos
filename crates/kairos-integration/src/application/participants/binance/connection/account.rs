//! Account capability handles projected from a Binance principal context.

use super::*;

pub struct BinanceSpotAccountEvents {
    pub(super) inner: BinanceSpotAsyncAccountEventSource,
}

pub struct BinanceFuturesAccountEvents {
    pub(super) inner: BinanceFuturesAsyncAccountEventSource,
}

pub struct BinanceOptionsAccountEvents {
    pub(super) inner: BinanceOptionsAsyncAccountEventSource,
}

pub struct BinanceMarginAccountEvents {
    pub(super) inner: BinanceAsyncMarginAccountEventSource,
}

pub struct BinanceSpotAccountRead {
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceSpotAccountMarketProfile {
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceSpotCredentialInspection {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

/// Funding-wallet projection from the shared provider/principal context.
pub struct BinanceFundingAccountRead {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceFundingCredentialInspection {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: BinanceSpotAccountClient,
}

pub struct BinanceMarginAccountRead {
    pub(super) client: BinanceSpotAccountClient,
    pub(super) domain: ConnectionDomain,
}

pub struct BinanceMarginCredentialInspection {
    pub(super) client: BinanceSpotAccountClient,
    pub(super) domain: ConnectionDomain,
}

pub struct BinanceFuturesAccountRead {
    pub(super) client: BinanceFuturesAccountClient,
}

pub struct BinanceFuturesCredentialInspection {
    pub(super) client: BinanceFuturesAccountClient,
    pub(super) domain: ConnectionDomain,
}

pub struct BinanceOptionsAccountRead {
    pub(super) client: BinanceOptionsAccountClient,
}

pub struct BinanceOptionsCredentialInspection {
    pub(super) client: BinanceOptionsAccountClient,
}

impl BinanceFundingAccountRead {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceSpotCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceFundingCredentialInspection {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl BinanceFuturesPrincipalConnection {
    pub fn account_events(
        &self,
        segment_key: impl Into<String>,
        config: &BinanceFuturesChannelConfig,
    ) -> Result<BinanceFuturesAccountEvents, IntegrationError> {
        BinanceFuturesAsyncAccountEventSource::new(
            format!("{}.account-events", self.descriptor.binding_id),
            segment_key,
            self.client.clone(),
            config.websocket_stream_url.clone(),
            config.event_queue_capacity,
        )
        .map(|inner| BinanceFuturesAccountEvents { inner })
    }

    pub fn account_read(&self) -> BinanceFuturesAccountRead {
        BinanceFuturesAccountRead {
            client: self.client.clone(),
        }
    }

    pub fn credential_inspection(&self) -> BinanceFuturesCredentialInspection {
        BinanceFuturesCredentialInspection {
            client: self.client.clone(),
            domain: self.domain,
        }
    }
}

impl BinanceMarginPrincipalConnection {
    pub fn account_events(
        &self,
        segment_key: impl Into<String>,
        config: &BinanceMarginChannelConfig,
    ) -> Result<BinanceMarginAccountEvents, IntegrationError> {
        BinanceAsyncMarginAccountEventSource::new(
            format!("{}.account-events", self.descriptor.binding_id),
            segment_key,
            self.client.clone(),
            config.websocket_stream_url.clone(),
            config.isolated_symbol.clone(),
            config.event_queue_capacity,
        )
        .map(|inner| BinanceMarginAccountEvents { inner })
    }

    pub fn account_read(&self) -> BinanceMarginAccountRead {
        BinanceMarginAccountRead {
            client: self.client.clone(),
            domain: self.domain,
        }
    }

    pub fn credential_inspection(&self) -> BinanceMarginCredentialInspection {
        BinanceMarginCredentialInspection {
            client: self.client.clone(),
            domain: self.domain,
        }
    }
}

impl BinanceOptionsPrincipalConnection {
    pub fn account_events(
        &self,
        segment_key: impl Into<String>,
        config: &BinanceOptionsChannelConfig,
    ) -> Result<BinanceOptionsAccountEvents, IntegrationError> {
        BinanceOptionsAsyncAccountEventSource::new(
            format!("{}.account-events", self.descriptor.binding_id),
            segment_key,
            self.client.clone(),
            config.websocket_stream_url.clone(),
            config.event_queue_capacity,
        )
        .map(|inner| BinanceOptionsAccountEvents { inner })
    }

    pub fn account_read(&self) -> BinanceOptionsAccountRead {
        BinanceOptionsAccountRead {
            client: self.client.clone(),
        }
    }

    pub fn credential_inspection(&self) -> BinanceOptionsCredentialInspection {
        BinanceOptionsCredentialInspection {
            client: self.client.clone(),
        }
    }
}

impl AsyncAccountEventSource for BinanceSpotAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEventEnvelope,
        IntegrationError,
    > {
        self.inner.next_account_event().await
    }
}

impl AsyncAccountEventSource for BinanceFuturesAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEventEnvelope,
        IntegrationError,
    > {
        self.inner.next_account_event().await
    }
}

impl AsyncAccountEventSource for BinanceOptionsAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEventEnvelope,
        IntegrationError,
    > {
        self.inner.next_account_event().await
    }
}

impl AsyncAccountEventSource for BinanceMarginAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> crate::domain::ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountEventEnvelope,
        IntegrationError,
    > {
        self.inner.next_account_event().await
    }
}

impl AsyncAccountReadConnection for BinanceSpotAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let account = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let orders = self
            .client
            .open_orders_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_account(segment, &account, &orders).map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountReadConnection for BinanceFundingAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let payload = self
            .client
            .signed_post_query_async("/sapi/v1/asset/get-funding-asset", Default::default())
            .await
            .map_err(map_exchange_error)?;
        funding_account::normalize_funding(segment, &payload)
            .map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountReadConnection for BinanceMarginAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let (payload, isolated) = match self.domain {
            ConnectionDomain::CrossMargin => (
                self.client
                    .signed_get_async("/sapi/v1/margin/account", Default::default())
                    .await
                    .map_err(map_exchange_error)?,
                false,
            ),
            ConnectionDomain::IsolatedMargin => (
                self.client
                    .signed_get_async("/sapi/v1/margin/isolated/account", Default::default())
                    .await
                    .map_err(map_exchange_error)?,
                true,
            ),
            _ => unreachable!(),
        };
        let orders = self
            .client
            .signed_get_async("/sapi/v1/margin/openOrders", Default::default())
            .await
            .map_err(map_exchange_error)?;
        crate::services::participants::binance::margin::normalize(
            segment, &payload, &orders, isolated,
        )
        .map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountReadConnection for BinanceFuturesAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let account = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let positions = self
            .client
            .positions_async()
            .await
            .map_err(map_exchange_error)?;
        let orders = self
            .client
            .open_orders_async()
            .await
            .map_err(map_exchange_error)?;
        crate::services::participants::binance::futures::account::normalize_account(
            segment, &account, &positions, &orders,
        )
        .map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountReadConnection for BinanceOptionsAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::capabilities::account_facts::ExternalAccountSegment,
    ) -> Result<
        crate::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        let account = self
            .client
            .request_async(
                "/eapi/v1/account",
                Default::default(),
                crate::services::participants::binance::options::account::Method::Get,
            )
            .await
            .map_err(map_exchange_error)?;
        let orders = self
            .client
            .request_async(
                "/eapi/v1/openOrders",
                Default::default(),
                crate::services::participants::binance::options::account::Method::Get,
            )
            .await
            .map_err(map_exchange_error)?;
        crate::services::participants::binance::options::account::normalize_account(
            segment, &account, &orders,
        )
        .map_err(IntegrationError::InvalidPayload)
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceSpotCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        Ok(
            crate::services::participants::binance::spot::account::normalize_credential_profile(
                &payload,
            ),
        )
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceFundingCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        Ok(funding_account::normalize_credential_profile(&payload))
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceMarginCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let mut profile =
            crate::services::participants::binance::spot::account::normalize_credential_profile(
                &payload,
            );
        profile.segments = vec![match self.domain {
            ConnectionDomain::CrossMargin => "cross_margin",
            ConnectionDomain::IsolatedMargin => "isolated_margin",
            _ => unreachable!(),
        }
        .into()];
        Ok(profile)
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceFuturesCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let mut permissions = vec!["read".into()];
        if payload
            .get("canTrade")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
        {
            permissions.push("trade".into());
        }
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type: payload
                .get("accountType")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned),
            permissions,
            segments: vec![match self.domain {
                ConnectionDomain::UsdMFutures => "usd_m_futures",
                ConnectionDomain::CoinMFutures => "coin_m_futures",
                _ => unreachable!(),
            }
            .into()],
            attributes: Default::default(),
        })
    }
}

impl AsyncAccountCredentialInspectionConnection for BinanceOptionsCredentialInspection {
    async fn inspect_credential(
        &mut self,
    ) -> Result<ExternalAccountCredentialProfile, IntegrationError> {
        let payload = self
            .client
            .request_async(
                "/eapi/v1/account",
                Default::default(),
                crate::services::participants::binance::options::account::Method::Get,
            )
            .await
            .map_err(map_exchange_error)?;
        Ok(ExternalAccountCredentialProfile {
            remote_identity: None,
            account_type: payload
                .get("accountType")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned),
            permissions: vec!["read".into()],
            segments: vec!["options".into()],
            attributes: Default::default(),
        })
    }
}

impl AsyncAccountMarketProfileConnection for BinanceSpotAccountMarketProfile {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        if request.source_symbol.as_str().trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance market profile symbol is required".into(),
            ));
        }
        let fee = self
            .client
            .trade_fee_async(request.source_symbol.as_str())
            .await
            .map_err(map_exchange_error)?;
        let account = self
            .client
            .account_async()
            .await
            .map_err(map_exchange_error)?;
        let burn = self
            .client
            .bnb_burn_status_async()
            .await
            .map_err(map_exchange_error)?;
        normalize_market_profile(request, &fee, &account, &burn)
            .map_err(IntegrationError::InvalidPayload)
    }
}
