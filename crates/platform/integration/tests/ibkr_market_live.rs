//! Opt-in read-only certification against a running TWS or IB Gateway.
//!
//! Required: `IBKR_HOST`, `IBKR_PORT`, and an API-enabled TWS/Gateway session.
//! Optional: `IBKR_CLIENT_ID` (default 91), `IBKR_SYMBOL` (AAPL),
//! `IBKR_EXCHANGE` (SMART), `IBKR_CURRENCY` (USD), and
//! `IBKR_MARKET_DATA_LINE_LIMIT` (100). The login must have the applicable
//! live or delayed market-data permission.

use std::future::poll_fn;
use std::time::Duration;

use kairos_integration::participants::ibkr::{IbkrMarketDataConfig, IbkrMarketDataConnection};
use kairos_integration::{
    ConnectionKey, ConnectionLifecycleCommand, MarketDataKind, MarketDataStream, MarketEventKind,
    MarketFeed, MarketSubscriptionCommand, MarketSubscriptionOutcome, MarketSubscriptionRequest,
};
use kairos_primitives::integration::ParticipantSymbol;

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("{name} is required for IBKR live certification"))
}

#[tokio::test]
#[ignore = "requires a running, API-enabled TWS or IB Gateway session"]
async fn quote_subscribe_receive_reconnect_and_unsubscribe() {
    let symbol = std::env::var("IBKR_SYMBOL").unwrap_or_else(|_| "AAPL".into());
    let mut connection = IbkrMarketDataConnection::new(
        ConnectionKey::new("ibkr-market-live-certification").unwrap(),
        IbkrMarketDataConfig {
            environment: "read-only-certification".into(),
            host: required("IBKR_HOST"),
            port: required("IBKR_PORT")
                .parse()
                .expect("IBKR_PORT must be u16"),
            client_id: std::env::var("IBKR_CLIENT_ID")
                .unwrap_or_else(|_| "91".into())
                .parse()
                .expect("IBKR_CLIENT_ID must be i32"),
            exchange: std::env::var("IBKR_EXCHANGE").unwrap_or_else(|_| "SMART".into()),
            currency: std::env::var("IBKR_CURRENCY").unwrap_or_else(|_| "USD".into()),
            market_data_line_limit: std::env::var("IBKR_MARKET_DATA_LINE_LIMIT")
                .unwrap_or_else(|_| "100".into())
                .parse()
                .expect("IBKR_MARKET_DATA_LINE_LIMIT must be usize"),
        },
    )
    .unwrap();
    tokio::time::timeout(Duration::from_secs(20), connection.connect())
        .await
        .expect("IBKR connect timed out")
        .unwrap();
    let feed = MarketFeed {
        kind: MarketDataKind::Quote,
        symbol: Some(ParticipantSymbol::new(&symbol).unwrap()),
        interval: None,
        depth: None,
        update_speed_millis: None,
    };
    let subscription = match connection
        .subscribe(MarketSubscriptionRequest::new(vec![feed]).unwrap())
        .await
        .unwrap()
    {
        MarketSubscriptionOutcome::Confirmed(subscription) => subscription,
        other => panic!("IBKR subscription was not confirmed: {other:?}"),
    };
    receive(&mut connection, &symbol).await;
    tokio::time::timeout(Duration::from_secs(30), connection.reconnect())
        .await
        .expect("IBKR reconnect timed out")
        .unwrap();
    receive(&mut connection, &symbol).await;
    assert!(matches!(
        connection.unsubscribe(subscription.id).await.unwrap(),
        MarketSubscriptionOutcome::Confirmed(())
    ));
    connection.disconnect().await.unwrap();
}

async fn receive(connection: &mut IbkrMarketDataConnection, symbol: &str) {
    tokio::time::timeout(Duration::from_secs(60), async {
        loop {
            let event = poll_fn(|cx| connection.poll_next(cx)).await.unwrap();
            if event.symbol.as_str().eq_ignore_ascii_case(symbol)
                && event.kind == MarketEventKind::Quote
                && (event.price.is_some() || event.ask_price.is_some())
            {
                return;
            }
        }
    })
    .await
    .expect("timed out waiting for an IBKR quote");
}
