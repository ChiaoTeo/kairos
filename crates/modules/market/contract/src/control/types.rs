use kairos_primitives::{
    AssetClass, Exchange, InstrumentId, InstrumentKind, MarketId, SourceId, StrategyId,
    SubscriptionId,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketCommandEnvelope<T> {
    pub schema_version: u16,
    pub command_id: String,
    pub idempotency_key: String,
    pub operation: String,
    pub strategy_id: StrategyId,
    #[serde(default)]
    pub launch_id: Option<String>,
    pub instance_id: String,
    pub payload: T,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscribePayload {
    pub subject: String,
    pub selectors: Vec<String>,
    #[serde(default)]
    pub source_id: Option<SourceId>,
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

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketRestRequest {
    Health,
    DataSources(MarketDataSourcesQuery),
    Subscribe(MarketCommandEnvelope<MarketSubscribePayload>),
    Unsubscribe(MarketCommandEnvelope<MarketUnsubscribePayload>),
    ReleaseOwner(MarketCommandEnvelope<MarketReleaseOwnerPayload>),
    Recover,
    PauseReplay,
    ResumeReplay,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MarketRestResponse {
    Health(Result<MarketHealthResponse, MarketControlError>),
    DataSources(Result<MarketDataSourcesResponse, MarketControlError>),
    Subscribe(Result<MarketSubscriptionResponse, MarketControlError>),
    Unsubscribe(Result<MarketCommandStatus, MarketControlError>),
    ReleaseOwner(Result<MarketReleaseOwnerResponse, MarketControlError>),
    Recover(Result<MarketCommandStatus, MarketControlError>),
    PauseReplay(Result<MarketCommandStatus, MarketControlError>),
    ResumeReplay(Result<MarketCommandStatus, MarketControlError>),
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSourcesQuery {
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub exchange: Option<Exchange>,
    pub market_type: Option<InstrumentKind>,
    pub asset_type: Option<AssetClass>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketHealthResponse {
    pub status: String,
    pub actor_id: String,
    pub event_sequence: u64,
    pub feed_status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSource {
    pub source_id: SourceId,
    pub status: String,
    pub ready: bool,
    pub stale: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataSourcesResponse {
    pub sources: Vec<MarketDataSource>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionResponse {
    pub subscription_id: SubscriptionId,
    pub owner_id: String,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketReleaseOwnerResponse {
    pub released_subscriptions: Vec<SubscriptionId>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketCommandStatus {
    pub status: String,
}

#[cfg(test)]
mod tests {
    use super::{MarketCommandEnvelope, MarketSubscribePayload};

    #[test]
    fn subscription_command_has_a_typed_contract_shape() {
        let command = MarketCommandEnvelope {
            schema_version: 2,
            command_id: "command-1".into(),
            idempotency_key: "key-1".into(),
            operation: "market.subscribe".into(),
            strategy_id: kairos_primitives::StrategyId::new("strategy-1").unwrap(),
            launch_id: Some("launch-1".into()),
            instance_id: "instance-1".into(),
            payload: MarketSubscribePayload {
                subject: "BTCUSDT".into(),
                selectors: vec!["trades".into()],
                source_id: Some(kairos_primitives::SourceId::new("binance-spot").unwrap()),
                exchange: Some(kairos_primitives::Exchange::new("binance").unwrap()),
                market_type: Some(kairos_primitives::InstrumentKind::Spot),
                asset_type: None,
                params: Default::default(),
                dynamic: false,
            },
        };
        let value = serde_json::to_value(command).unwrap();
        assert_eq!(value["operation"], "market.subscribe");
        assert_eq!(value["payload"]["selectors"][0], "trades");
        assert!(value.get("payload").is_some());
    }
}
