use super::*;
use futures_util::{SinkExt, StreamExt};

fn principal() -> OkxPrincipalConnection {
    OkxConnection::connect(OkxConnectionConfig {
        environment: "paper".into(),
        rest_base_url: "https://example.test".into(),
        shared_quota: None,
    })
    .unwrap()
    .principal_connection(OkxPrincipalConfig {
        binding_id: "account.okx.main".into(),
        principal_id: Some("principal-1".into()),
        api_key: "api-key".into(),
        secret: "secret".into(),
        passphrase: "passphrase".into(),
        quota: None,
        order_quota: None,
    })
    .unwrap()
}

fn provider() -> OkxConnection {
    OkxConnection::connect(OkxConnectionConfig {
        environment: "paper".into(),
        rest_base_url: "https://example.test".into(),
        shared_quota: None,
    })
    .unwrap()
}

#[test]
fn trading_capabilities_share_participant_domain_without_capability_metadata() {
    let principal = principal();
    let account = principal.trading_account(InstrumentType::Spot);
    let inspection = principal.trading_credential_inspection(InstrumentType::Spot);
    let profile = principal.trading_account_market_profile(InstrumentType::Spot);
    assert_eq!(account.descriptor(), inspection.descriptor());
    assert_eq!(account.descriptor(), profile.descriptor());
    assert_eq!(account.descriptor().domain.as_str(), "trading");
    assert_eq!(account.descriptor().participant.id.as_str(), "okx");
}

#[test]
fn instrument_type_is_not_connection_domain() {
    let principal = principal();
    let spot = principal.trading_descriptor(InstrumentType::Spot);
    let swap = principal.trading_descriptor(InstrumentType::Swap);
    assert_eq!(spot.domain, swap.domain);
    assert_ne!(spot.binding_id, swap.binding_id);
}

#[test]
fn order_traits_are_the_capability_boundary_and_td_mode_is_validated() {
    fn is_async_entry<T: AsyncOrderEntryConnection>(_: &T) {}
    fn is_async_query<T: AsyncOrderQueryConnection>(_: &T) {}
    fn is_blocking_entry<T: OrderEntryConnection>(_: &T) {}
    fn is_blocking_query<T: OrderQueryConnection>(_: &T) {}

    let principal = principal();
    let entry = principal
        .trading_order_entry(InstrumentType::Spot, TradingMode::Cash)
        .unwrap();
    let query = principal.trading_order_query(InstrumentType::Spot);
    let blocking_entry = principal
        .blocking_trading_order_entry(InstrumentType::Spot, TradingMode::Cash)
        .unwrap();
    let blocking_query = principal.blocking_trading_order_query(InstrumentType::Spot);
    is_async_entry(&entry);
    is_async_query(&query);
    is_blocking_entry(&blocking_entry);
    is_blocking_query(&blocking_query);
    assert_eq!(entry.descriptor().domain.as_str(), "trading");
    assert!(principal
        .trading_order_entry(InstrumentType::Spot, TradingMode::Cross)
        .is_err());
    assert!(principal
        .trading_order_entry(InstrumentType::Margin, TradingMode::Cash)
        .is_err());
}

#[test]
fn public_catalog_returns_provider_facts_without_canonical_identity() {
    fn is_async<T: AsyncInstrumentCatalogConnection>(_: &T) {}
    fn is_blocking<T: InstrumentCatalogConnection>(_: &T) {}
    fn is_async_snapshot<T: AsyncMarketSnapshotConnection>(_: &T) {}
    let provider = provider();
    let catalog = provider.instrument_catalog(InstrumentType::Swap);
    let blocking = provider.blocking_instrument_catalog(InstrumentType::Swap);
    let snapshot = provider.market_snapshot(InstrumentType::Swap);
    is_async(&catalog);
    is_blocking(&blocking);
    is_async_snapshot(&snapshot);
    assert_eq!(catalog.descriptor().domain.as_str(), "market-data");
    assert!(catalog.descriptor().principal_id.is_none());
    assert_eq!(catalog.descriptor(), snapshot.descriptor());

    let facts = normalize_instrument_catalog(
        InstrumentType::Swap,
        &serde_json::json!({
            "code": "0",
            "data": [{
                "instId": "BTC-USDT-SWAP",
                "uly": "BTC-USDT",
                "settleCcy": "USDT",
                "state": "live",
                "tickSz": "0.1",
                "lotSz": "0.01",
                "minSz": "0.01",
                "ctVal": "0.01"
            }]
        }),
    )
    .unwrap();
    assert_eq!(facts.instruments[0].source_symbol, "BTC-USDT-SWAP");
    assert_eq!(facts.instruments[0].kind, ExternalInstrumentKind::Perpetual);
    assert_eq!(
        facts.instruments[0].settlement_currency.as_deref(),
        Some("USDT")
    );
}

#[test]
fn public_catalog_skips_unaddressable_preopen_placeholders() {
    let facts = normalize_instrument_catalog(
        InstrumentType::Futures,
        &serde_json::json!({
            "code": "0",
            "data": [
                {
                    "instId": "",
                    "instFamily": "OP-USD_UM_XPERP",
                    "state": "preopen",
                    "listTime": "1786440600000"
                },
                {
                    "instId": "BTC-USD-260925",
                    "uly": "BTC-USD",
                    "settleCcy": "BTC",
                    "state": "live",
                    "expTime": "1790323200000"
                }
            ]
        }),
    )
    .unwrap();

    assert_eq!(facts.instruments.len(), 1);
    assert_eq!(facts.instruments[0].source_symbol, "BTC-USD-260925");
}

#[tokio::test(flavor = "current_thread")]
async fn public_catalog_uses_the_callers_runtime() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0_u8; 4096];
        let read = stream.read(&mut request).await.unwrap();
        let request = String::from_utf8_lossy(&request[..read]);
        assert!(request.starts_with("GET /api/v5/public/instruments?instType=SWAP "));

        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","uly":"BTC-USDT","settleCcy":"USDT","state":"live"}]}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });

    let mut catalog = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: format!("http://{address}"),
        shared_quota: None,
    })
    .unwrap()
    .instrument_catalog(InstrumentType::Swap);
    let facts = catalog.fetch_instruments().await.unwrap();
    assert_eq!(facts.instruments[0].source_symbol, "BTC-USDT-SWAP");
    server.await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn option_catalog_enumerates_underlyings_before_fetching_instruments() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        for index in 0..3 {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = vec![0_u8; 4096];
            let read = stream.read(&mut request).await.unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            let body = match index {
                0 => {
                    assert!(request.starts_with("GET /api/v5/public/underlying?instType=OPTION "));
                    r#"{"code":"0","data":[["BTC-USD","ETH-USD"]]}"#
                }
                1 => {
                    assert!(request.starts_with(
                        "GET /api/v5/public/instruments?instType=OPTION&uly=BTC-USD "
                    ));
                    r#"{"code":"0","data":[{"instId":"BTC-USD-260925-50000-C","uly":"BTC-USD","settleCcy":"BTC","expTime":"1790294400000","stk":"50000","optType":"C","state":"live","tickSz":"0.001","lotSz":"0.01","minSz":"0.01","ctVal":"0.01"}]}"#
                }
                _ => {
                    assert!(request.starts_with(
                        "GET /api/v5/public/instruments?instType=OPTION&uly=ETH-USD "
                    ));
                    r#"{"code":"0","data":[{"instId":"ETH-USD-260925-5000-P","uly":"ETH-USD","settleCcy":"ETH","expTime":"1790294400000","stk":"5000","optType":"P","state":"live","tickSz":"0.001","lotSz":"0.01","minSz":"0.01","ctVal":"0.1"}]}"#
                }
            };
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).await.unwrap();
        }
    });

    let mut catalog = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: format!("http://{address}"),
        shared_quota: None,
    })
    .unwrap()
    .instrument_catalog(InstrumentType::Option);
    let facts = catalog.fetch_instruments().await.unwrap();

    assert_eq!(facts.instruments.len(), 2);
    assert!(facts
        .instruments
        .iter()
        .all(|instrument| instrument.kind == ExternalInstrumentKind::Option));
    server.await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn public_market_snapshot_uses_the_callers_runtime() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0_u8; 4096];
        let read = stream.read(&mut request).await.unwrap();
        let request = String::from_utf8_lossy(&request[..read]);
        assert!(request.starts_with("GET /api/v5/market/ticker?instId=BTC-USDT-SWAP "));
        let body = r#"{"code":"0","data":[{"instId":"BTC-USDT-SWAP","bidPx":"60000.1","bidSz":"2","askPx":"60000.2","askSz":"3"}]}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });

    let mut snapshot = OkxConnection::connect(OkxConnectionConfig {
        environment: "public".into(),
        rest_base_url: format!("http://{address}"),
        shared_quota: None,
    })
    .unwrap()
    .market_snapshot(InstrumentType::Swap);
    let symbols = [ProviderSymbol::new("BTC-USDT-SWAP").unwrap()];
    let events = snapshot.fetch_snapshot(&symbols).await.unwrap();
    assert_eq!(events[0].symbol, "BTC-USDT-SWAP");
    assert_eq!(events[0].kind, MarketEventKind::Quote);
    server.await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn private_account_events_use_the_callers_runtime_and_answer_ping() {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        let _login = socket.next().await.unwrap().unwrap();
        socket
            .send(Message::Text(r#"{"event":"login","code":"0"}"#.into()))
            .await
            .unwrap();
        let _subscribe = socket.next().await.unwrap().unwrap();
        socket
            .send(Message::Text(
                r#"{"event":"subscribe","arg":{"channel":"account"},"code":"0"}"#.into(),
            ))
            .await
            .unwrap();
        socket.send(Message::Ping(vec![4, 2].into())).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Pong(payload) if payload == vec![4, 2]
        ));
        // A provider may begin publishing one subscribed channel before the
        // other acknowledgement arrives. Connect must preserve this fact.
        socket
            .send(Message::Text(
                r#"{"arg":{"channel":"account"},"data":[{"ccy":"USDT","eq":"10.5"}]}"#.into(),
            ))
            .await
            .unwrap();
        socket
            .send(Message::Text(
                r#"{"event":"subscribe","arg":{"channel":"orders"},"code":"0"}"#.into(),
            ))
            .await
            .unwrap();
        // Keep the peer alive until the client has consumed the buffered
        // account fact and closes the channel. Otherwise a parallel test run
        // can observe the TCP reset before the queued acknowledgements.
        let _ = socket.next().await;
    });
    let mut events = principal()
        .trading_account_events(
            InstrumentType::Spot,
            "spot",
            &OkxPrivateChannelConfig {
                websocket_url: format!("ws://{address}"),
                event_queue_capacity: 8,
            },
        )
        .unwrap();
    events.connect_channel().await.unwrap();
    let event = events.next_account_event().await.unwrap();
    assert_eq!(event.channel_id, "okx-private:spot:account");
    assert_eq!(event.channel_epoch, 1);
    assert!(event.received_at_unix_nanos >= event.observed_at_unix_nanos);
    let crate::application::capabilities::account_facts::ExternalAccountEvent::Snapshot(snapshot) =
        event.payload
    else {
        panic!("expected snapshot")
    };
    assert_eq!(snapshot.balances[0].total.mantissa, 105);
    events.disconnect_channel().await.unwrap();
    server.await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn private_order_events_use_the_callers_runtime_and_preserve_route_binding() {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
        let _login = socket.next().await.unwrap().unwrap();
        socket
            .send(Message::Text(r#"{"event":"login","code":"0"}"#.into()))
            .await
            .unwrap();
        let subscribe = socket.next().await.unwrap().unwrap();
        assert!(subscribe.to_string().contains(r#""channel":"orders""#));
        socket.send(Message::Ping(vec![8, 1].into())).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Pong(payload) if payload == vec![8, 1]
        ));
        socket
            .send(Message::Text(
                r#"{"arg":{"channel":"orders"},"data":[{"clOrdId":"local-1","ordId":"88","instId":"BTC-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"limit","state":"live","sz":"1","accFillSz":"0","uTime":"1700000000000"}]}"#.into(),
            ))
            .await
            .unwrap();
    });
    let mut events = principal()
        .trading_order_events(
            InstrumentType::Swap,
            TradingMode::Cross,
            &OkxPrivateChannelConfig {
                websocket_url: format!("ws://{address}"),
                event_queue_capacity: 8,
            },
        )
        .unwrap();
    events.connect_channel().await.unwrap();
    let event = events.next_order_event().await.unwrap();
    assert_eq!(event.binding_id, "account.okx.main.trading.swap.cross");
    assert_eq!(event.channel_epoch, 1);
    assert_eq!(event.payload.order_id, "local-1");
    events.disconnect_channel().await.unwrap();
    server.await.unwrap();
}
