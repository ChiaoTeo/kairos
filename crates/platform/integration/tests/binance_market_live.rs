//! Opt-in production endpoint certification for Binance public market streams.
//!
//! Run with:
//! `cargo test -p kairos-integration --test binance_market_live -- --ignored --nocapture`

use std::future::poll_fn;
use std::time::Duration;

use kairos_integration::participants::binance::BinanceWebSocketConfig;
use kairos_integration::participants::binance::coinm::BinanceCoinMWebSocketConnection;
use kairos_integration::participants::binance::options::BinanceOptionsWebSocketConnection;
use kairos_integration::participants::binance::spot::BinanceSpotWebSocketConnection;
use kairos_integration::participants::binance::usdm::BinanceUsdMWebSocketConnection;
use kairos_integration::{
    ConnectionKey, ConnectionLifecycleCommand, MarketDataKind, MarketDataStream, MarketEvent,
    MarketEventKind, MarketFeed, MarketSubscriptionCommand, MarketSubscriptionOutcome,
    MarketSubscriptionRequest,
};
use kairos_primitives::integration::ParticipantSymbol;

fn config(endpoint: &str) -> BinanceWebSocketConfig {
    BinanceWebSocketConfig {
        environment: "production-public-certification".into(),
        endpoint: endpoint.into(),
        credential: None,
        event_capacity: 4_096,
    }
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

async fn subscribe<C>(
    connection: &mut C,
    feeds: Vec<MarketFeed>,
) -> kairos_integration::MarketSubscription
where
    C: MarketSubscriptionCommand,
{
    let outcome = connection
        .subscribe(MarketSubscriptionRequest::new(feeds).unwrap())
        .await
        .unwrap();
    match outcome {
        MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
        other => panic!("subscription was not confirmed: {other:?}"),
    }
}

async fn receive<C>(connection: &mut C, symbol: &str, accepted: &[MarketEventKind]) -> MarketEvent
where
    C: MarketDataStream,
{
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let event = poll_fn(|cx| connection.poll_next(cx)).await.unwrap();
            if event.symbol.as_str() == symbol && accepted.contains(&event.kind) {
                return event;
            }
        }
    })
    .await
    .expect("timed out waiting for a matching Binance market event")
}

async fn certify<C>(
    connection: &mut C,
    feeds: Vec<MarketFeed>,
    symbol: &str,
    accepted: &[MarketEventKind],
) where
    C: ConnectionLifecycleCommand + MarketSubscriptionCommand + MarketDataStream,
{
    let mut connect_error = None;
    for _ in 0..3 {
        match connection.connect().await {
            Ok(()) => {
                connect_error = None;
                break;
            },
            Err(error) => {
                connect_error = Some(error);
                let _ = connection.disconnect().await;
                tokio::time::sleep(Duration::from_millis(500)).await;
            },
        }
    }
    assert!(connect_error.is_none(), "connect failed: {connect_error:?}");
    let subscription = subscribe(connection, feeds).await;
    receive(connection, symbol, accepted).await;

    let mut reconnect_error = None;
    for _ in 0..3 {
        match connection.reconnect().await {
            Ok(()) => {
                reconnect_error = None;
                break;
            },
            Err(error) => {
                reconnect_error = Some(error);
                tokio::time::sleep(Duration::from_millis(500)).await;
            },
        }
    }
    assert!(
        reconnect_error.is_none(),
        "reconnect failed: {reconnect_error:?}"
    );
    receive(connection, symbol, accepted).await;

    match connection.unsubscribe(subscription.id).await.unwrap() {
        MarketSubscriptionOutcome::Confirmed(()) => {},
        other => panic!("unsubscription was not confirmed: {other:?}"),
    }
    connection.disconnect().await.unwrap();
}

#[tokio::test]
#[ignore = "uses Binance production public WebSocket endpoints"]
async fn spot_subscribe_receive_reconnect_and_unsubscribe() {
    let mut connection = BinanceSpotWebSocketConnection::new(
        ConnectionKey::new("binance-spot-live-certification").unwrap(),
        config("wss://stream.binance.com:9443/ws"),
    )
    .unwrap();
    certify(
        &mut connection,
        vec![feed(MarketDataKind::Trade, "BTCUSDT")],
        "BTCUSDT",
        &[MarketEventKind::Trade],
    )
    .await;
}

#[tokio::test]
#[ignore = "uses Binance production public WebSocket endpoints"]
async fn usdm_subscribe_receive_reconnect_and_unsubscribe() {
    let mut connection = BinanceUsdMWebSocketConnection::new(
        ConnectionKey::new("binance-usdm-live-certification").unwrap(),
        config("wss://fstream.binance.com"),
    )
    .unwrap();
    certify(
        &mut connection,
        vec![feed(MarketDataKind::MarkPrice, "BTCUSDT")],
        "BTCUSDT",
        &[MarketEventKind::MarkPrice],
    )
    .await;
}

#[tokio::test]
#[ignore = "uses Binance production public WebSocket endpoints"]
async fn coinm_subscribe_receive_reconnect_and_unsubscribe() {
    let mut connection = BinanceCoinMWebSocketConnection::new(
        ConnectionKey::new("binance-coinm-live-certification").unwrap(),
        config("wss://dstream.binance.com/ws"),
    )
    .unwrap();
    certify(
        &mut connection,
        vec![feed(MarketDataKind::Quote, "BTCUSD_PERP")],
        "BTCUSD_PERP",
        &[MarketEventKind::Quote],
    )
    .await;
}

#[tokio::test]
#[ignore = "uses Binance production public REST and WebSocket endpoints"]
async fn options_public_and_market_routes_receive_after_reconnect() {
    let exchange_info: serde_json::Value =
        reqwest::get("https://eapi.binance.com/eapi/v1/exchangeInfo")
            .await
            .unwrap()
            .json()
            .await
            .unwrap();
    let symbol = exchange_info["optionSymbols"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["underlying"] == "BTCUSDT" && row["status"] == "TRADING")
        .and_then(|row| row["symbol"].as_str())
        .expect("Binance returned no active BTC option")
        .to_owned();

    let mut connection = BinanceOptionsWebSocketConnection::new(
        ConnectionKey::new("binance-options-live-certification").unwrap(),
        config("wss://fstream.binance.com"),
    )
    .unwrap();
    certify(
        &mut connection,
        vec![
            feed(MarketDataKind::Quote, &symbol),
            feed(MarketDataKind::MarkPrice, &symbol),
        ],
        &symbol,
        &[MarketEventKind::Quote, MarketEventKind::MarkPrice],
    )
    .await;
}
