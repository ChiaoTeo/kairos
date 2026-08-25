//! Opt-in production endpoint certification for Hyperliquid public market streams.
//!
//! Run with:
//! `cargo test -p kairos-integration --test hyperliquid_market_live -- --ignored --nocapture --test-threads=1`

use std::future::poll_fn;
use std::time::Duration;

use kairos_integration::participants::hyperliquid::{
    HyperliquidWebSocketConfig, HyperliquidWebSocketConnection,
};
use kairos_integration::{
    ConnectionKey, ConnectionLifecycleCommand, MarketDataKind, MarketDataStream, MarketEventKind,
    MarketFeed, MarketSubscriptionCommand, MarketSubscriptionOutcome, MarketSubscriptionRequest,
};
use kairos_primitives::integration::ParticipantSymbol;

fn connection(label: &str) -> HyperliquidWebSocketConnection {
    HyperliquidWebSocketConnection::new(
        ConnectionKey::new(label).unwrap(),
        HyperliquidWebSocketConfig {
            environment: "production-public-certification".into(),
            endpoint: "wss://api.hyperliquid.xyz/ws".into(),
            event_capacity: 4_096,
            user: None,
        },
    )
    .unwrap()
}

fn feed(kind: MarketDataKind, symbol: &str) -> MarketFeed {
    MarketFeed {
        kind,
        symbol: Some(ParticipantSymbol::new(symbol).unwrap()),
        interval: None,
        depth: None,
        update_speed_millis: None,
    }
}

async fn certify(
    connection: &mut HyperliquidWebSocketConnection,
    feed: MarketFeed,
    symbol: &str,
    expected: MarketEventKind,
) {
    let mut connect_error = None;
    for _ in 0..3 {
        match tokio::time::timeout(Duration::from_secs(20), connection.connect()).await {
            Ok(Ok(())) => {
                connect_error = None;
                break;
            },
            Ok(Err(error)) => connect_error = Some(error.to_string()),
            Err(_) => connect_error = Some("connect timed out".into()),
        }
        let _ = connection.disconnect().await;
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    assert!(connect_error.is_none(), "connect failed: {connect_error:?}");
    let subscription = match tokio::time::timeout(
        Duration::from_secs(20),
        connection.subscribe(MarketSubscriptionRequest::new(vec![feed]).unwrap()),
    )
    .await
    .expect("Hyperliquid subscribe timed out")
    .unwrap()
    {
        MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
        other => panic!("subscription was not confirmed: {other:?}"),
    };
    receive(connection, symbol, expected).await;
    let mut reconnect_error = None;
    for _ in 0..3 {
        match tokio::time::timeout(Duration::from_secs(20), connection.reconnect()).await {
            Ok(Ok(())) => {
                reconnect_error = None;
                break;
            },
            Ok(Err(error)) => reconnect_error = Some(error.to_string()),
            Err(_) => reconnect_error = Some("reconnect timed out".into()),
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    assert!(
        reconnect_error.is_none(),
        "reconnect failed: {reconnect_error:?}"
    );
    receive(connection, symbol, expected).await;
    assert!(matches!(
        tokio::time::timeout(
            Duration::from_secs(20),
            connection.unsubscribe(subscription.id)
        )
        .await
        .expect("Hyperliquid unsubscribe timed out")
        .unwrap(),
        MarketSubscriptionOutcome::Confirmed(())
    ));
    tokio::time::timeout(Duration::from_secs(5), connection.disconnect())
        .await
        .expect("Hyperliquid disconnect timed out")
        .unwrap();
}

async fn receive(
    connection: &mut HyperliquidWebSocketConnection,
    symbol: &str,
    expected: MarketEventKind,
) {
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            let event = poll_fn(|cx| connection.poll_next(cx)).await.unwrap();
            if event.symbol.as_str() == symbol && event.kind == expected {
                return;
            }
        }
    })
    .await
    .expect("timed out waiting for a matching Hyperliquid quote");
}

async fn active_spot_coin() -> String {
    let response: serde_json::Value = reqwest::Client::new()
        .post("https://api.hyperliquid.xyz/info")
        .json(&serde_json::json!({"type":"spotMetaAndAssetCtxs"}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let universe = response[0]["universe"].as_array().unwrap();
    response[1]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .filter_map(|(index, context)| {
            let volume = context["dayNtlVlm"].as_str()?.parse::<f64>().ok()?;
            let name = universe.get(index)?["name"].as_str()?;
            Some((volume, name))
        })
        .max_by(|left, right| left.0.total_cmp(&right.0))
        .map(|(_, name)| name.to_owned())
        .expect("Hyperliquid returned no active spot market")
}

#[tokio::test]
#[ignore = "uses Hyperliquid production public WebSocket endpoints"]
async fn perpetual_subscribe_receive_reconnect_and_unsubscribe() {
    certify(
        &mut connection("hyperliquid-perpetual-live-certification"),
        feed(MarketDataKind::Quote, "BTC"),
        "BTC",
        MarketEventKind::Quote,
    )
    .await;
}

#[tokio::test]
#[ignore = "uses Hyperliquid production public REST and WebSocket endpoints"]
async fn spot_subscribe_receive_reconnect_and_unsubscribe() {
    let coin = active_spot_coin().await;
    certify(
        &mut connection("hyperliquid-spot-live-certification"),
        feed(MarketDataKind::OrderBook, &coin),
        &coin,
        MarketEventKind::BookSnapshot,
    )
    .await;
}
