//! Sole asynchronous task serializing all MarketActor mutations.

use std::collections::BTreeMap;
use std::time::Duration;

use kairos_protocol::InstanceIdentity;
use serde_json::Value;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::{self, MissedTickBehavior};
use tracing::{error, info, warn};

use super::publication::{MarketChangePublisher, MarketHistoryRecorder};
use crate::application::MarketApplication;
use crate::services::control::EngineCommand;
use crate::services::publication::EventPublication;
use crate::services::publication::MarketEventEncoder;
use crate::services::source::SourceActivator;
pub(super) struct MarketActorTask {
    pub(super) application: MarketApplication,
    pub(super) publisher: Box<dyn MarketChangePublisher>,
    pub(super) event_actor_id: String,
    pub(super) event_identity: InstanceIdentity,
    pub(super) event_encoder: MarketEventEncoder,
    pub(super) publication_interval: Duration,
    pub(super) freshness_check_interval: Duration,
    pub(super) freshness_max_age: Duration,
    pub(super) shutdown_timeout: Duration,
    pub(super) stop_requested: bool,
    pub(super) command_results: BTreeMap<String, CachedCommandResult>,
    pub(super) source_activator: Option<Box<dyn SourceActivator>>,
    pub(super) history_recorder: MarketHistoryRecorder,
}

#[derive(Clone)]
pub(super) struct CachedCommandResult {
    pub(super) request_body: String,
    pub(super) status: u16,
    pub(super) payload: Value,
}

impl MarketActorTask {
    pub(super) async fn run_actor_loop(
        mut self,
        mut control_receiver: Receiver<EngineCommand>,
        event_sender: Sender<Vec<u8>>,
    ) -> Result<(), String> {
        info!(
            event = "actor_task_starting",
            component = "market",
            "market actor task starting"
        );
        let mut event_publication = EventPublication::new(
            self.event_actor_id.clone(),
            self.event_identity.clone(),
            event_sender,
            self.event_encoder,
        );
        let mut freshness_ticks = time::interval(self.freshness_check_interval);
        freshness_ticks.set_missed_tick_behavior(MissedTickBehavior::Delay);
        while !self.stop_requested {
            let sources_enabled = self.application.has_sources();
            tokio::select! {
                Some(command) = control_receiver.recv() => {
                    self.handle_engine_command(command).await?;
                }
                input = self.application.next_source_input(), if sources_enabled => {
                    let Some(input) = input else {
                        return Err("market source input channel closed".into());
                    };
                    self.handle_source_input(input).await?;
                }
                _ = freshness_ticks.tick() => {
                    self.run_maintenance();
                }
            }
            self.publish_changes(&mut event_publication).await?;
            // Retry a bounded publication queue whenever any engine input or
            // maintenance wake-up occurs. Finite replay must not wait forever
            // merely because its final event first encountered a full queue.
            event_publication.flush()?;
            if self.application.sources_complete() && event_publication.is_empty() {
                self.stop_requested = true;
            }
        }
        self.shutdown(&mut event_publication).await?;
        info!(
            event = "actor_task_stopped",
            component = "market",
            "market actor task stopped"
        );
        Ok(())
    }
}

pub(super) fn log_event(level: &str, message: &str, fields: Value) {
    match level {
        "error" => error!(component = "market", event = "runtime", fields = %fields, "{message}"),
        "warn" => warn!(component = "market", event = "runtime", fields = %fields, "{message}"),
        _ => info!(component = "market", event = "runtime", fields = %fields, "{message}"),
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::super::{MarketChangePublisher, MarketProcess};
    use crate::composition::publication::encode_event;
    use crate::domain::observation::{
        Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks,
        Ticker24h,
    };
    use crate::{MarketApplication, MarketEvent, OrderBook, PriceLevel};
    use kairos_protocol::InstanceIdentity;
    use serde_json::json;
    use std::time::Duration;

    struct NullPublisher;

    impl MarketChangePublisher for NullPublisher {
        fn publish(&mut self, _change: &crate::domain::events::MarketChange) -> Result<(), String> {
            Ok(())
        }
    }

    fn test_process(
        application: MarketApplication,
        socket_path: impl Into<std::path::PathBuf>,
        event_socket_path: impl Into<std::path::PathBuf>,
        interval: Duration,
    ) -> MarketProcess {
        MarketProcess::new_configured_with_activator(
            application,
            NullPublisher,
            socket_path,
            event_socket_path,
            InstanceIdentity::default(),
            super::super::lifecycle::MarketProcessSettings {
                publication_interval: interval,
                freshness_check_interval: interval,
                freshness_max_age: Duration::from_secs(5),
                reference_recovery_interval: interval,
                shutdown_timeout: Duration::from_secs(5),
                publication_queue_capacity: 256,
            },
            None,
            crate::composition::publication::encode_event,
        )
        .unwrap()
    }

    #[test]
    fn bar_and_greeks_have_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let bar = MarketObservation::Bar(Bar {
            scope: crate::ObservationScope::market("market:btc").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc").unwrap(),
            timeframe: "1m".into(),
            open: "1".parse().unwrap(),
            high: "2".parse().unwrap(),
            low: "0.5".parse().unwrap(),
            close: "1.5".parse().unwrap(),
            volume: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
            source_id: "binance".into(),
            derivation: "aggregated".into(),
        });
        let greeks = MarketObservation::OptionGreeks(OptionGreeks {
            scope: crate::ObservationScope::market("market:btc-option").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("instrument:btc-option").unwrap(),
            expiry_unix_nanos: None,
            strike: None,
            delta: Some("0.5".parse().unwrap()),
            gamma: None,
            vega: None,
            theta: None,
            implied_volatility: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(2),
            source_id: "deribit".into(),
            derivation: "direct".into(),
        });
        assert_eq!(
            &encode_event("actor", &identity, 1, &MarketEvent::Observation(bar)).unwrap()[4..8],
            b"MBV2"
        );
        assert_eq!(
            &encode_event("actor", &identity, 2, &MarketEvent::Observation(greeks)).unwrap()[4..8],
            b"MGU2"
        );
    }

    #[test]
    fn orderbook_mutation_has_a_sequence_preserving_event_wire_message() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let book = OrderBook::snapshot_with_source(
            "binance-spot",
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC",
            42,
            100,
            vec![PriceLevel {
                price: "64000".parse().unwrap(),
                quantity: "1.25".parse().unwrap(),
            }],
            vec![PriceLevel {
                price: "64001".parse().unwrap(),
                quantity: "2.5".parse().unwrap(),
            }],
        )
        .unwrap();

        let encoded =
            encode_event("actor", &identity, 7, &MarketEvent::OrderBookSnapshot(book)).unwrap();

        assert_eq!(&encoded[4..8], b"MOS2");
        let decoded = kairos_market_contract::event::decode_event(&encoded).unwrap();
        let kairos_market_contract::event::MarketEvent::OrderBookSnapshotReceived(decoded) =
            decoded
        else {
            panic!("wrong v2 event root")
        };
        assert_eq!(decoded.metadata().sequence(), 7);
        assert_eq!(decoded.snapshot().sequence(), 42);
    }

    #[test]
    fn derivative_observations_have_distinct_event_wire_messages() {
        let identity = InstanceIdentity::new("workspace", "launch", "instance");
        let common = (
            kairos_primitives::MarketId::new("market:btc").unwrap(),
            kairos_primitives::InstrumentId::new("instrument:btc").unwrap(),
            "source".to_string(),
        );
        let ticker = MarketObservation::Ticker24h(Ticker24h {
            scope: crate::ObservationScope::from(common.0.clone()),
            instrument_id: common.1.clone(),
            last_price: Some("1".parse().unwrap()),
            bid_price: None,
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            open_price: None,
            high_price: None,
            low_price: None,
            volume_base: None,
            volume_quote: None,
            price_change_abs: None,
            price_change_pct: None,
            vwap: None,
            mark_price: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
            source_id: common.2.clone(),
        });
        let mark = MarketObservation::MarkPrice(MarkPrice {
            scope: crate::ObservationScope::from(common.0.clone()),
            instrument_id: common.1.clone(),
            mark_price: "1".parse().unwrap(),
            index_price: None,
            estimated_settlement_price: None,
            funding_rate: None,
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(2),
            source_id: common.2.clone(),
        });
        let index = MarketObservation::IndexPrice(IndexPrice {
            scope: crate::ObservationScope::from(common.0.clone()),
            instrument_id: common.1.clone(),
            spot_index_price: Some("1".parse().unwrap()),
            contract_index_price: None,
            index_price: None,
            funding_rate: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(3),
            source_id: common.2.clone(),
        });
        let funding = MarketObservation::FundingRate(FundingRate {
            scope: crate::ObservationScope::from(common.0.clone()),
            instrument_id: common.1.clone(),
            funding_rate: "0.001".parse().unwrap(),
            funding_period_seconds: Some(28_800),
            next_funding_time_unix_nanos: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(4),
            source_id: common.2.clone(),
        });
        let open_interest = MarketObservation::OpenInterest(OpenInterest {
            scope: crate::ObservationScope::from(common.0),
            instrument_id: common.1,
            contracts: "10".parse().unwrap(),
            quote_value: None,
            change_24h: None,
            change_pct_24h: None,
            observed_at_unix_nanos: kairos_primitives::UnixNanos::new(5),
            source_id: common.2,
        });
        for (sequence, observation, identifier) in [
            (1, ticker, b"MTU2"),
            (2, mark, b"MMP2"),
            (3, index, b"MIP2"),
            (4, funding, b"MFR2"),
            (5, open_interest, b"MOI2"),
        ] {
            assert_eq!(
                &encode_event(
                    "actor",
                    &identity,
                    sequence,
                    &MarketEvent::Observation(observation)
                )
                .unwrap()[4..8],
                identifier
            );
        }
    }

    #[tokio::test]
    async fn command_idempotency_replays_same_result_and_rejects_payload_reuse() {
        let root = tempfile::tempdir().unwrap();
        let mut process = test_process(
            MarketApplication::new("test-market", 10).unwrap(),
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        );
        let body = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "command-1",
            "idempotency_key": "idem-1",
            "operation": "market.subscribe",
            "strategy_id": "strategy-1",
            "instance_id": "instance-1",
            "payload": {
                "subject": "market.BTCUSDT",
                "selectors": ["quote"],
                "exchange": "binance",
                "market_type": "spot",
                "asset_type": "crypto",
                "params": {"market_id":"market:binance:spot:BTCUSDT","instrument_id":"instrument:binance:spot:BTCUSDT"},
                "dynamic": false
            }
        }))
        .unwrap();
        let first = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &body)
            .await;
        let second = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &body)
            .await;
        assert_eq!(first.status, second.status);
        assert_eq!(first.payload, second.payload);
        let conflict = body.replace("BTCUSDT", "ETHUSDT");
        let response = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &conflict)
            .await;
        assert_eq!(response.status, 409);
    }

    #[tokio::test]
    async fn consolidated_subscription_accepts_an_instrument_route_without_a_market() {
        let root = tempfile::tempdir().unwrap();
        let mut process = test_process(
            MarketApplication::new("test-market", 10).unwrap(),
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        );
        let body = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "massive-aapl-quotes",
            "idempotency_key": "massive-aapl-quotes",
            "operation": "market.subscribe",
            "strategy_id": "strategy-1",
            "instance_id": "instance-1",
            "payload": {
                "subject": "AAPL",
                "selectors": ["quote"],
                "source_id": "massive-equity",
                "market_type": "equity",
                "params": {
                    "scope": "consolidated",
                    "instrument_id": "instrument:equity:US:AAPL:common",
                    "provider_id": "massive",
                    "network_id": "sip"
                },
                "dynamic": false
            }
        }))
        .unwrap();

        let response = process
            .actor_task
            .handle_request("POST", "/v1/subscribe", &body)
            .await;

        assert_eq!(response.status, 202, "{}", response.payload);
        let subscription = process
            .actor_task
            .application
            .current_view()
            .subscriptions
            .into_iter()
            .next()
            .expect("subscription");
        let target = subscription.members.values().next().expect("target");
        assert!(target.market_id().is_none());
        assert_eq!(
            target.scope.key(),
            "consolidated:instrument:equity:US:AAPL:common:sip"
        );
    }

    #[tokio::test]
    async fn owner_release_is_scoped_idempotent_and_enforced_by_unsubscribe() {
        let root = tempfile::tempdir().unwrap();
        let mut application = MarketApplication::new("test-market", 10).unwrap();
        crate::composition::attach_replay_source_with_policy(
            &mut application,
            std::iter::empty::<MarketObservation>(),
            None,
            None,
            root.path().join("owner-checkpoint.json"),
            crate::composition::MarketReplayClock::Maximum,
            1,
            true,
        )
        .unwrap();
        let mut process = test_process(
            application,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        );
        let subscribe = |command_id: &str, launch_id: &str, strategy_id: &str| {
            serde_json::to_string(&json!({
                "schema_version": 1,
                "command_id": command_id,
                "idempotency_key": command_id,
                "operation": "market.subscribe",
                "strategy_id": strategy_id,
                "launch_id": launch_id,
                "instance_id": "instance-1",
                "payload": {
                    "subject": "market.BTCUSDT",
                    "selectors": ["quote"],
                    "exchange": "binance",
                    "market_type": "spot",
                    "asset_type": "crypto",
                    "params": {"market_id":"market:binance:spot:BTCUSDT","instrument_id":"instrument:binance:spot:BTCUSDT"},
                    "dynamic": false
                }
            }))
            .unwrap()
        };
        for body in [
            subscribe("subscription-a", "launch-a", "same-name"),
            subscribe("subscription-b", "launch-b", "same-name"),
        ] {
            assert_eq!(
                process
                    .actor_task
                    .handle_request("POST", "/v1/subscribe", &body)
                    .await
                    .status,
                202
            );
        }
        let wrong_owner_unsubscribe = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "wrong-owner-unsubscribe",
            "idempotency_key": "wrong-owner-unsubscribe",
            "operation": "market.unsubscribe",
            "strategy_id": "same-name",
            "launch_id": "launch-b",
            "instance_id": "instance-1",
            "payload": {"subscription_id": "subscription-a"}
        }))
        .unwrap();
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/unsubscribe", &wrong_owner_unsubscribe)
                .await
                .status,
            409
        );

        let release = serde_json::to_string(&json!({
            "schema_version": 1,
            "command_id": "release-a",
            "idempotency_key": "release-a",
            "operation": "market.release_owner",
            "strategy_id": "same-name",
            "launch_id": "launch-a",
            "instance_id": "instance-1",
            "payload": {}
        }))
        .unwrap();
        let released = process
            .actor_task
            .handle_request("POST", "/v1/subscriptions/release-owner", &release)
            .await;
        assert_eq!(released.status, 200);
        assert_eq!(
            released.payload["removed_subscription_ids"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let subscriptions = process.actor_task.application.current_view().subscriptions;
        assert_eq!(subscriptions.len(), 1);
        assert_eq!(subscriptions[0].id.0, "subscription-b");

        let repeated = release.replace("release-a", "release-a-again");
        let released = process
            .actor_task
            .handle_request("POST", "/v1/subscriptions/release-owner", &repeated)
            .await;
        assert_eq!(released.status, 200);
        assert_eq!(
            released.payload["removed_subscription_ids"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
    }

    #[tokio::test]
    async fn replay_pause_resume_controls_the_source_without_a_polling_path() {
        let root = tempfile::tempdir().unwrap();
        let mut application = MarketApplication::new("replay-market", 10).unwrap();
        crate::composition::attach_replay_source_with_policy(
            &mut application,
            [MarketObservation::Bar(crate::Bar {
                scope: crate::ObservationScope::market("market:test:spot:TEST").unwrap(),
                instrument_id: kairos_primitives::InstrumentId::new("instrument:test:spot:TEST")
                    .unwrap(),
                timeframe: "1m".into(),
                open: "1".parse().unwrap(),
                high: "1".parse().unwrap(),
                low: "1".parse().unwrap(),
                close: "1".parse().unwrap(),
                volume: None,
                observed_at_unix_nanos: kairos_primitives::UnixNanos::new(1),
                source_id: "replay".into(),
                derivation: "acceptance".into(),
            })],
            None,
            None,
            root.path().join("checkpoint.json"),
            crate::composition::MarketReplayClock::Maximum,
            1,
            true,
        )
        .unwrap();
        let mut process = test_process(
            application,
            root.path().join("market.sock"),
            root.path().join("market.events.sock"),
            Duration::from_millis(10),
        );
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(
            process
                .actor_task
                .application
                .current_view()
                .sources
                .values()
                .next()
                .unwrap()
                .status,
            crate::SourceStatus::Paused
        );

        let subscribe = json!({
            "schema_version":1,"command_id":"replay-sub","idempotency_key":"replay-sub",
            "operation":"market.subscribe","strategy_id":"acceptance","instance_id":"instance",
            "payload":{"subject":"market.TEST","selectors":["bar"],"exchange":"test",
            "market_type":"spot","asset_type":"crypto","params":{"market_id":"market:test:spot:TEST","instrument_id":"instrument:test:spot:TEST"},"dynamic":false}
        })
        .to_string();
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/subscribe", &subscribe)
                .await
                .status,
            202
        );
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(process.actor_task.application.event_sequence(), 0);
        assert_eq!(
            process
                .actor_task
                .handle_request("POST", "/v1/replay/resume", "")
                .await
                .status,
            202
        );
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        process
            .actor_task
            .application
            .drive_next_source_input()
            .await
            .unwrap();
        assert_eq!(process.actor_task.application.event_sequence(), 1);
    }
}
