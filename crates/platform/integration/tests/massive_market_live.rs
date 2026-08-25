//! Opt-in production certification for each Massive WebSocket product.
//!
//! `MASSIVE_API_KEY` is required. Options and futures additionally require
//! `MASSIVE_OPTIONS_SYMBOL` and `MASSIVE_FUTURES_SYMBOL`, so certification
//! never relies on a contract that may have expired. Every endpoint may be
//! overridden with `MASSIVE_<PRODUCT>_ENDPOINT` for delayed or entitled tiers.

use std::future::poll_fn;
use std::time::Duration;

use kairos_integration::participants::massive::{
    MassiveCryptoWebSocketConnection, MassiveForexWebSocketConnection,
    MassiveFuturesWebSocketConnection, MassiveIndicesWebSocketConnection,
    MassiveOptionsWebSocketConnection, MassiveStocksWebSocketConnection, MassiveWebSocketConfig,
};
use kairos_integration::{
    ConnectionKey, ConnectionLifecycleCommand, MarketDataKind, MarketDataStream, MarketEventKind,
    MarketFeed, MarketSubscriptionCommand, MarketSubscriptionOutcome, MarketSubscriptionRequest,
};
use kairos_primitives::integration::ParticipantSymbol;
use secrecy::SecretString;

fn config(product: &str, default_endpoint: &str) -> MassiveWebSocketConfig {
    let api_key = std::env::var("MASSIVE_API_KEY")
        .expect("MASSIVE_API_KEY is required for Massive live certification");
    let endpoint = std::env::var(format!("MASSIVE_{}_ENDPOINT", product.to_ascii_uppercase()))
        .unwrap_or_else(|_| default_endpoint.into());
    MassiveWebSocketConfig {
        environment: "production-read-only-certification".into(),
        endpoint,
        api_key: SecretString::from(api_key),
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

async fn certify<C>(connection: &mut C, feed: MarketFeed, symbol: &str, expected: MarketEventKind)
where
    C: ConnectionLifecycleCommand + MarketSubscriptionCommand + MarketDataStream,
{
    tokio::time::timeout(Duration::from_secs(20), connection.connect())
        .await
        .expect("Massive connect timed out")
        .unwrap();
    let subscription = match tokio::time::timeout(
        Duration::from_secs(20),
        connection.subscribe(MarketSubscriptionRequest::new(vec![feed]).unwrap()),
    )
    .await
    .expect("Massive subscribe timed out")
    .unwrap()
    {
        MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
        other => panic!("Massive subscription was not confirmed: {other:?}"),
    };
    receive(connection, symbol, expected).await;
    tokio::time::timeout(Duration::from_secs(20), connection.reconnect())
        .await
        .expect("Massive reconnect timed out")
        .unwrap();
    receive(connection, symbol, expected).await;
    assert!(matches!(
        tokio::time::timeout(
            Duration::from_secs(20),
            connection.unsubscribe(subscription.id)
        )
        .await
        .expect("Massive unsubscribe timed out")
        .unwrap(),
        MarketSubscriptionOutcome::Confirmed(())
    ));
    tokio::time::timeout(Duration::from_secs(5), connection.disconnect())
        .await
        .expect("Massive disconnect timed out")
        .unwrap();
}

async fn receive<C>(connection: &mut C, symbol: &str, expected: MarketEventKind)
where
    C: MarketDataStream,
{
    tokio::time::timeout(Duration::from_secs(60), async {
        loop {
            let event = poll_fn(|cx| connection.poll_next(cx)).await.unwrap();
            if event.symbol.as_str().eq_ignore_ascii_case(symbol) && event.kind == expected {
                return;
            }
        }
    })
    .await
    .expect("timed out waiting for a matching Massive event");
}

macro_rules! certification {
    ($test:ident, $connection:ty, $product:literal, $endpoint:literal, $symbol:expr, $kind:expr, $event:expr) => {
        #[tokio::test]
        #[ignore = "requires a Massive API key and product entitlement"]
        async fn $test() {
            let symbol = $symbol;
            let mut connection = <$connection>::new(
                ConnectionKey::new(concat!("massive-", $product, "-certification")).unwrap(),
                config($product, $endpoint),
            )
            .unwrap();
            certify(&mut connection, feed($kind, &symbol), &symbol, $event).await;
        }
    };
}

certification!(
    stocks_subscribe_receive_reconnect_and_unsubscribe,
    MassiveStocksWebSocketConnection,
    "stocks",
    "wss://socket.massive.com/stocks",
    std::env::var("MASSIVE_STOCKS_SYMBOL").unwrap_or_else(|_| "AAPL".into()),
    MarketDataKind::Quote,
    MarketEventKind::Quote
);
certification!(
    options_subscribe_receive_reconnect_and_unsubscribe,
    MassiveOptionsWebSocketConnection,
    "options",
    "wss://socket.massive.com/options",
    std::env::var("MASSIVE_OPTIONS_SYMBOL")
        .expect("MASSIVE_OPTIONS_SYMBOL must name an active option contract"),
    MarketDataKind::Quote,
    MarketEventKind::Quote
);
certification!(
    futures_subscribe_receive_reconnect_and_unsubscribe,
    MassiveFuturesWebSocketConnection,
    "futures",
    "wss://socket.massive.com/futures",
    std::env::var("MASSIVE_FUTURES_SYMBOL")
        .expect("MASSIVE_FUTURES_SYMBOL must name an active futures contract"),
    MarketDataKind::Quote,
    MarketEventKind::Quote
);
certification!(
    indices_subscribe_receive_reconnect_and_unsubscribe,
    MassiveIndicesWebSocketConnection,
    "indices",
    "wss://business.massive.com/indices",
    std::env::var("MASSIVE_INDICES_SYMBOL").unwrap_or_else(|_| "I:SPX".into()),
    MarketDataKind::IndexPrice,
    MarketEventKind::IndexPrice
);
certification!(
    forex_subscribe_receive_reconnect_and_unsubscribe,
    MassiveForexWebSocketConnection,
    "forex",
    "wss://socket.massive.com/forex",
    std::env::var("MASSIVE_FOREX_SYMBOL").unwrap_or_else(|_| "C:EURUSD".into()),
    MarketDataKind::Quote,
    MarketEventKind::Quote
);
certification!(
    crypto_subscribe_receive_reconnect_and_unsubscribe,
    MassiveCryptoWebSocketConnection,
    "crypto",
    "wss://socket.massive.com/crypto",
    std::env::var("MASSIVE_CRYPTO_SYMBOL").unwrap_or_else(|_| "X:BTC-USD".into()),
    MarketDataKind::Trade,
    MarketEventKind::Trade
);
