//! Opt-in production endpoint certification for OKX public market streams.
//!
//! Run with:
//! `cargo test -p kairos-integration --test okx_market_live -- --ignored --nocapture --test-threads=1`

use std::future::poll_fn;
use std::time::Duration;

use kairos_integration::participants::okx::OkxWebSocketConfig;
use kairos_integration::participants::okx::public::OkxPublicWebSocketConnection;
use kairos_integration::{
    ConnectionKey, ConnectionLifecycleCommand, MarketDataKind, MarketDataStream, MarketEvent,
    MarketEventKind, MarketFeed, MarketSubscriptionCommand, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};
use kairos_primitives::integration::ParticipantSymbol;

fn connection(label: &str) -> OkxPublicWebSocketConnection {
    OkxPublicWebSocketConnection::new(
        ConnectionKey::new(label).unwrap(),
        OkxWebSocketConfig {
            environment: "production-public-certification".into(),
            endpoint: "wss://ws.okx.com:8443/ws/v5/public".into(),
            event_capacity: 4_096,
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

async fn receive<C>(connection: &mut C, symbol: &str, kind: MarketEventKind) -> MarketEvent
where
    C: MarketDataStream,
{
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            let event = poll_fn(|cx| connection.poll_next(cx)).await.unwrap();
            if event.symbol.as_str() == symbol && event.kind == kind {
                return event;
            }
        }
    })
    .await
    .expect("timed out waiting for a matching OKX market event")
}

async fn certify(
    connection: &mut OkxPublicWebSocketConnection,
    feed: MarketFeed,
    symbol: &str,
    kind: MarketEventKind,
) {
    connection.connect().await.unwrap();
    let subscription = match connection
        .subscribe(MarketSubscriptionRequest::new(vec![feed]).unwrap())
        .await
        .unwrap()
    {
        MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
        other => panic!("subscription was not confirmed: {other:?}"),
    };
    receive(connection, symbol, kind).await;
    connection.reconnect().await.unwrap();
    receive(connection, symbol, kind).await;
    assert!(matches!(
        connection.unsubscribe(subscription.id).await.unwrap(),
        MarketSubscriptionOutcome::Confirmed(())
    ));
    connection.disconnect().await.unwrap();
}

async fn active_instrument(instrument_type: &str) -> String {
    let family = (instrument_type == "OPTION").then_some("&instFamily=BTC-USD");
    let response: serde_json::Value = reqwest::get(format!(
        "https://www.okx.com/api/v5/public/instruments?instType={instrument_type}{}",
        family.unwrap_or_default()
    ))
    .await
    .unwrap()
    .json()
    .await
    .unwrap();
    response["data"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["state"] == "live")
        .and_then(|row| row["instId"].as_str())
        .unwrap_or_else(|| panic!("OKX returned no live {instrument_type} instrument"))
        .to_owned()
}

#[tokio::test]
#[ignore = "uses OKX production public WebSocket endpoints"]
async fn spot_subscribe_receive_reconnect_and_unsubscribe() {
    certify(
        &mut connection("okx-spot-live-certification"),
        feed(MarketDataKind::Trade, "BTC-USDT"),
        "BTC-USDT",
        MarketEventKind::Trade,
    )
    .await;
}

#[tokio::test]
#[ignore = "uses OKX production public WebSocket endpoints"]
async fn swap_subscribe_receive_reconnect_and_unsubscribe() {
    certify(
        &mut connection("okx-swap-live-certification"),
        feed(MarketDataKind::MarkPrice, "BTC-USDT-SWAP"),
        "BTC-USDT-SWAP",
        MarketEventKind::MarkPrice,
    )
    .await;
}

#[tokio::test]
#[ignore = "uses OKX production public REST and WebSocket endpoints"]
async fn futures_subscribe_receive_reconnect_and_unsubscribe() {
    let symbol = active_instrument("FUTURES").await;
    certify(
        &mut connection("okx-futures-live-certification"),
        feed(MarketDataKind::Quote, &symbol),
        &symbol,
        MarketEventKind::Quote,
    )
    .await;
}

#[tokio::test]
#[ignore = "uses OKX production public REST and WebSocket endpoints"]
async fn options_subscribe_receive_reconnect_and_unsubscribe() {
    let symbol = active_instrument("OPTION").await;
    certify(
        &mut connection("okx-options-live-certification"),
        feed(MarketDataKind::Greeks, &symbol),
        &symbol,
        MarketEventKind::Greeks,
    )
    .await;
}
