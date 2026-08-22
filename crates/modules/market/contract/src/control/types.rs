use kairos_primitives::integration::ProviderId;
use kairos_primitives::market::{ObservationKind, SourceId, SubscriptionId};
use kairos_primitives::reference::{AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId};
use kairos_primitives::runtime::{IdempotencyKey, InstanceId, LaunchId, RequestId, StrategyId};
use kairos_primitives::time::Sequence;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketCommandEnvelope<T> {
    pub schema_version: u16,
    pub command_id: RequestId,
    pub idempotency_key: IdempotencyKey,
    pub operation: MarketOperation,
    pub strategy_id: StrategyId,
    #[serde(default)]
    pub launch_id: Option<LaunchId>,
    pub instance_id: InstanceId,
    pub payload: T,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketOperation {
    Subscribe,
    Unsubscribe,
    ReleaseOwner,
    Recover,
    PauseReplay,
    ResumeReplay,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscribePayload {
    pub subject: String,
    pub selectors: Vec<String>,
    #[serde(default)]
    pub source_id: Option<SourceId>,
    #[serde(default)]
    pub source_ids: Vec<SourceId>,
    pub exchange: Option<Exchange>,
    pub market_type: Option<InstrumentKind>,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub params: std::collections::BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    pub dynamic: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketUnsubscribePayload {
    pub subscription_id: SubscriptionId,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketReleaseOwnerPayload {}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct MarketControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSourcesQuery {
    #[serde(default)]
    pub target: Option<String>,
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub underlying_market_id: Option<MarketId>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    pub exchange: Option<Exchange>,
    pub market_type: Option<InstrumentKind>,
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub observation_kind: Option<ObservationKind>,
    #[serde(default)]
    pub provider_id: Option<ProviderId>,
    #[serde(default)]
    pub configured_only: bool,
    #[serde(default)]
    pub ready_only: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketHealthResponse {
    pub status: MarketHealthStatus,
    pub actor_id: kairos_primitives::runtime::ActorId,
    pub event_sequence: Sequence,
    pub feed_status: MarketFeedStatus,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketHealthStatus {
    Ready,
    Degraded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketFeedStatus {
    Disconnected,
    Ready,
    Reconnecting,
    WarmingUp,
    Degraded,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSource {
    pub source_id: SourceId,
    #[serde(default)]
    pub provider_id: Option<ProviderId>,
    #[serde(default)]
    pub observation_capabilities: Vec<ObservationKind>,
    #[serde(default)]
    pub configured: bool,
    pub status: MarketSourceStatus,
    pub ready: bool,
    pub stale: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketSourceStatus {
    Connecting,
    Ready,
    Paused,
    Reconnecting,
    WarmingUp,
    Degraded,
    Disconnected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSourcesResponse {
    pub sources: Vec<MarketDataSource>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionResponse {
    pub subscription_id: SubscriptionId,
    pub owner_id: SubscriptionOwnerKey,
    pub status: MarketSubscriptionStatus,
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct SubscriptionOwnerKey(String);

impl SubscriptionOwnerKey {
    pub fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into();
        if value.is_empty() || value.trim() != value {
            return Err("subscription owner key must be non-empty and trimmed".into());
        }
        Ok(Self(value))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for SubscriptionOwnerKey {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        Self::new(String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

impl std::fmt::Display for SubscriptionOwnerKey {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketSubscriptionStatus {
    Pending,
    Ready,
    Degraded,
    Unavailable,
    Rejected,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketReleaseOwnerResponse {
    pub released_subscriptions: Vec<SubscriptionId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketCommandStatus {
    pub status: MarketCommandOutcome,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketCommandOutcome {
    Applied,
    Accepted,
    Recovered,
    Paused,
    Running,
}

#[cfg(test)]
mod tests {
    use kairos_primitives::runtime::{IdempotencyKey, InstanceId, LaunchId, RequestId};

    use super::{
        MarketCommandEnvelope, MarketDataSourcesQuery, MarketOperation, MarketSubscribePayload,
    };

    #[test]
    fn subscription_command_has_a_typed_contract_shape() {
        let command = MarketCommandEnvelope {
            schema_version: 2,
            command_id: RequestId::new("command-1").unwrap(),
            idempotency_key: IdempotencyKey::new("key-1").unwrap(),
            operation: MarketOperation::Subscribe,
            strategy_id: kairos_primitives::runtime::StrategyId::new("strategy-1").unwrap(),
            launch_id: Some(LaunchId::new("launch-1").unwrap()),
            instance_id: InstanceId::new("instance-1").unwrap(),
            payload: MarketSubscribePayload {
                subject: "BTCUSDT".into(),
                selectors: vec!["trades".into()],
                source_id: Some(kairos_primitives::market::SourceId::new("binance-spot").unwrap()),
                source_ids: Vec::new(),
                exchange: Some(kairos_primitives::reference::Exchange::new("binance").unwrap()),
                market_type: Some(kairos_primitives::reference::InstrumentKind::Spot),
                asset_type: None,
                params: Default::default(),
                dynamic: false,
            },
        };
        let value = serde_json::to_value(command).unwrap();
        assert_eq!(value["operation"], "subscribe");
        assert_eq!(value["payload"]["selectors"][0], "trades");
        assert!(value.get("payload").is_some());
    }

    #[test]
    fn data_source_query_uses_typed_discovery_filters() {
        let query: MarketDataSourcesQuery = serde_json::from_value(serde_json::json!({
            "market_id": "market:binance:spot:BTCUSDT",
            "instrument_id": "instrument:spot:BTC",
            "observation_kind": "quote",
            "provider_id": "binance",
            "configured_only": true,
            "ready_only": true
        }))
        .unwrap();

        assert_eq!(
            query.market_id.as_ref().map(|value| value.as_str()),
            Some("market:binance:spot:BTCUSDT")
        );
        assert_eq!(
            query.observation_kind,
            Some(kairos_primitives::market::ObservationKind::Quote)
        );
        assert_eq!(
            query.provider_id.as_ref().map(|value| value.as_str()),
            Some("binance")
        );
        assert!(query.configured_only);
        assert!(query.ready_only);
    }
}
