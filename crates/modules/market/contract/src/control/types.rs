use kairos_primitives::decimal::Price;
use kairos_primitives::market::{ObservationKind, Provider, SubscriptionId};
use kairos_primitives::reference::{InstrumentId, MarketId};
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketOperatorCommandEnvelope<T> {
    pub schema_version: u16,
    pub command_id: RequestId,
    pub idempotency_key: IdempotencyKey,
    pub operation: MarketOperation,
    pub owner_id: SubscriptionOwnerKey,
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
    pub target: MarketTarget,
    pub observations: std::collections::BTreeSet<ObservationRequirement>,
    #[serde(default)]
    pub provider_preference: ProviderPreference,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum MarketTarget {
    Market {
        market_id: MarketId,
    },
    ConsolidatedInstrument {
        instrument_id: InstrumentId,
        #[serde(default)]
        network_id: Option<String>,
    },
    Options {
        #[serde(default)]
        underlying_market_id: Option<MarketId>,
        #[serde(default)]
        underlying_instrument_id: Option<InstrumentId>,
        #[serde(default)]
        expiry_from_unix_nanos: Option<kairos_primitives::time::UnixNanos>,
        #[serde(default)]
        expiry_to_unix_nanos: Option<kairos_primitives::time::UnixNanos>,
        #[serde(default)]
        strike_lower: Option<Price>,
        #[serde(default)]
        strike_upper: Option<Price>,
        #[serde(default)]
        option_right: Option<String>,
        #[serde(default)]
        limit: Option<u32>,
        #[serde(default)]
        progressive: bool,
    },
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ObservationRequirement {
    pub kind: ObservationKind,
    #[serde(default)]
    pub qualifier: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "mode", content = "providers", rename_all = "snake_case")]
pub enum ProviderPreference {
    #[default]
    Automatic,
    Prefer(Vec<Provider>),
    Require(Vec<Provider>),
    AllEligible,
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
pub struct MarketDataRoutesQuery {
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub observation_kind: Option<ObservationKind>,
    #[serde(default)]
    pub provider: Option<Provider>,
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
    pub current_view_commit_count: u64,
    pub current_view_input_update_count: u64,
    pub current_view_encoded_update_count: u64,
    pub current_view_order_book_encode_count: u64,
    pub last_current_view_commit_latency_nanos: u64,
    pub notification_attempt_count: u64,
    pub notification_failure_count: u64,
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
pub struct MarketDataRoute {
    pub market_id: MarketId,
    pub provider: Provider,
    #[serde(default)]
    pub observation_kinds: Vec<ObservationKind>,
    pub state: MarketDataRouteState,
    pub selected: bool,
    #[serde(default)]
    pub pending_reason: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketDataRouteState {
    Supported,
    Configured,
    Ready,
    Degraded,
    Stopped,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketDataRoutesResponse {
    pub routes: Vec<MarketDataRoute>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionResponse {
    pub subscription_id: SubscriptionId,
    pub owner_id: SubscriptionOwnerKey,
    pub state: MarketSubscriptionState,
    #[serde(default)]
    pub satisfied: std::collections::BTreeSet<ObservationRequirement>,
    #[serde(default)]
    pub missing: std::collections::BTreeSet<ObservationRequirement>,
    #[serde(default)]
    pub resolved_providers: std::collections::BTreeSet<Provider>,
    #[serde(default)]
    pub pending_reason: Option<SubscriptionPendingReason>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionsQuery {
    #[serde(default)]
    pub owner_id: Option<SubscriptionOwnerKey>,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    #[serde(default)]
    pub state: Option<MarketSubscriptionState>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionSnapshot {
    pub subscription_id: SubscriptionId,
    pub owner_id: SubscriptionOwnerKey,
    pub state: MarketSubscriptionState,
    #[serde(default)]
    pub market_ids: Vec<MarketId>,
    #[serde(default)]
    pub observations: std::collections::BTreeSet<ObservationRequirement>,
    #[serde(default)]
    pub selected_providers: std::collections::BTreeSet<Provider>,
    #[serde(default)]
    pub pending_reason: Option<SubscriptionPendingReason>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct MarketSubscriptionsResponse {
    pub subscriptions: Vec<MarketSubscriptionSnapshot>,
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
pub enum MarketSubscriptionState {
    Resolving,
    Active,
    PartiallyActive,
    WaitingForProvider,
    WaitingForMarket,
    Degraded,
    Failed,
    Released,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "reason", rename_all = "snake_case")]
pub enum SubscriptionPendingReason {
    ResolvingUnderlying,
    WaitingForSpot,
    SelectingContracts,
    SubscribingMembers {
        selected: u32,
        active: u32,
        failed: u32,
    },
    MarketUnavailable,
    ProviderUnavailable {
        #[serde(default)]
        required: Vec<Provider>,
    },
    MissingObservations {
        observations: std::collections::BTreeSet<ObservationRequirement>,
    },
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
        MarketCommandEnvelope, MarketDataRoutesQuery, MarketOperation,
        MarketOperatorCommandEnvelope, MarketSubscribePayload, MarketSubscriptionsQuery,
        MarketTarget, ObservationRequirement, ProviderPreference, SubscriptionOwnerKey,
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
                target: MarketTarget::Market {
                    market_id: kairos_primitives::reference::MarketId::new(
                        "market:binance:spot:BTCUSDT",
                    )
                    .unwrap(),
                },
                observations: [ObservationRequirement {
                    kind: kairos_primitives::market::ObservationKind::Trade,
                    qualifier: None,
                }]
                .into_iter()
                .collect(),
                provider_preference: ProviderPreference::Automatic,
            },
        };
        let value = serde_json::to_value(command).unwrap();
        assert_eq!(value["operation"], "subscribe");
        assert_eq!(value["payload"]["observations"][0]["kind"], "trade");
        assert!(value["payload"].get("source_id").is_none());
        assert!(value.get("payload").is_some());
    }

    #[test]
    fn subscription_inventory_and_operator_commands_are_typed() {
        let owner = SubscriptionOwnerKey::new("operator:kairos-i:session-1").unwrap();
        let query: MarketSubscriptionsQuery = serde_json::from_value(serde_json::json!({
            "owner_id": owner.as_str(),
            "market_id": "market:binance:spot:BTCUSDT",
            "state": "active"
        }))
        .unwrap();
        assert_eq!(query.owner_id.as_ref(), Some(&owner));

        let command: MarketOperatorCommandEnvelope<MarketSubscribePayload> =
            serde_json::from_value(serde_json::json!({
                "schema_version": 1,
                "command_id": "command-1",
                "idempotency_key": "command-1",
                "operation": "subscribe",
                "owner_id": owner.as_str(),
                "payload": {
                    "target": {"type": "market", "market_id": "market:binance:spot:BTCUSDT"},
                    "observations": [{"kind": "quote"}],
                    "provider_preference": {"mode": "automatic"}
                }
            }))
            .unwrap();
        assert_eq!(command.owner_id, owner);
        assert_eq!(command.operation, MarketOperation::Subscribe);
    }

    #[test]
    fn data_route_query_uses_typed_discovery_filters() {
        let query: MarketDataRoutesQuery = serde_json::from_value(serde_json::json!({
            "market_id": "market:binance:spot:BTCUSDT",
            "instrument_id": "instrument:spot:BTC",
            "observation_kind": "quote",
            "provider": "binance",
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
            query.provider.as_ref().map(|value| value.as_str()),
            Some("binance")
        );
        assert!(query.configured_only);
        assert!(query.ready_only);
    }
}
