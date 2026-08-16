use super::{
    BinanceFuturesConnectionConfig, BinanceOptionsConnection, BinanceOptionsConnectionConfig,
    BinancePrincipalConfig, BinanceSpotConnection, BinanceSpotConnectionConfig,
    BinanceUsdMConnection, ConnectionDomain,
};
use crate::application::participants::binance::BinanceQuotaAllocation;
use secrecy::SecretString;

use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, InstrumentCatalogConnection,
};
use crate::application::capabilities::{
    DecimalValue, OrderEntryRequest, OrderSide, OrderType, ParticipantKind, ParticipantRef,
    ProviderInstrumentRef,
};
use crate::application::{
    AsyncAccountCredentialInspectionConnection, AsyncAccountReadConnection, AsyncEarnConnection,
    AsyncOrderEntryConnection, AsyncOrderQueryConnection, AsyncTransferConnection,
};

fn native_usdm_principal(
    base_url: String,
    binding_id: &str,
) -> super::BinanceFuturesPrincipalConnection {
    BinanceUsdMConnection::connect(BinanceFuturesConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: base_url,
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap()
    .principal_connection(BinancePrincipalConfig {
        binding_id: binding_id.into(),
        principal_id: Some("test".into()),
        api_key: SecretString::from("api-key"),
        secret: SecretString::from("secret"),
        principal_quota: None,
    })
    .unwrap()
}

fn native_options_principal(
    base_url: String,
    binding_id: &str,
) -> super::BinanceOptionsPrincipalConnection {
    BinanceOptionsConnection::connect(BinanceOptionsConnectionConfig {
        environment: "test".into(),
        rest_base_url: base_url,
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap()
    .principal_connection(BinancePrincipalConfig {
        binding_id: binding_id.into(),
        principal_id: Some("test".into()),
        api_key: SecretString::from("api-key"),
        secret: SecretString::from("secret"),
        principal_quota: None,
    })
    .unwrap()
}

fn futures_order_request(order_id: &str) -> OrderEntryRequest {
    OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new(order_id).unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("usd-m-futures").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-usdt").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::UsdMFutures.into()),
            "BTCUSDT",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(1, 3),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    }
}

fn margin_order_request(order_id: &str) -> OrderEntryRequest {
    OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new(order_id).unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("cross-margin").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-usdt").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::CrossMargin.into()),
            "BTCUSDT",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(1, 3),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    }
}

fn options_order_request(order_id: &str) -> OrderEntryRequest {
    OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new(order_id).unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("options").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-call").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::Options.into()),
            "BTC-260327-100000-C",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(1, 2),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    }
}

#[test]
fn native_connection_validates_binding_and_projects_shared_capabilities() {
    fn is_credential_inspection<T: AsyncAccountCredentialInspectionConnection>(_: &T) {}
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: "https://testnet.binance.vision".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let connection = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.primary".into(),
            principal_id: Some("account-a".into()),
            api_key: SecretString::from("key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();

    assert_eq!(connection.spot_descriptor().participant.id, "binance");
    assert_eq!(connection.spot_descriptor().domain.as_str(), "spot");
    assert_eq!(
        connection.spot_descriptor().principal_id.as_deref(),
        Some("account-a")
    );
    connection.spot_order_entry().unwrap();
    connection.spot_order_query().unwrap();
    let inspection = connection.spot_credential_inspection();
    is_credential_inspection(&inspection);
    assert_eq!(inspection.descriptor(), &connection.spot_descriptor());
    connection
        .spot_order_events(&super::BinanceSpotChannelConfig {
            websocket_api_url: "wss://ws-api.testnet.binance.vision/ws-api/v3".into(),
            event_queue_capacity: 1_024,
        })
        .unwrap();
}

#[test]
fn funding_handles_share_one_connection_domain_without_capability_metadata() {
    fn is_account_read<T: AsyncAccountReadConnection>(_: &T) {}
    fn is_credential_inspection<T: AsyncAccountCredentialInspectionConnection>(_: &T) {}
    fn is_earn<T: AsyncEarnConnection>(_: &T) {}
    fn is_transfer<T: AsyncTransferConnection>(_: &T) {}

    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: "https://testnet.binance.vision".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let connection = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "account.binance.main".into(),
            principal_id: Some("main".into()),
            api_key: SecretString::from("key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();

    let account_read = connection.funding_account_read();
    let inspection = connection.funding_credential_inspection();
    let earn = connection.earn();
    let transfer = connection.transfer();
    let blocking_account_read = connection.blocking_funding_account_read();
    let blocking_inspection = connection.blocking_funding_credential_inspection();
    let blocking_earn = connection.blocking_earn();
    let blocking_transfer = connection.blocking_transfer();
    is_account_read(&account_read);
    is_credential_inspection(&inspection);
    is_earn(&earn);
    is_transfer(&transfer);
    for descriptor in [
        account_read.descriptor(),
        inspection.descriptor(),
        earn.descriptor(),
        transfer.descriptor(),
    ] {
        assert_eq!(
            descriptor.domain.as_str(),
            ConnectionDomain::Funding.as_str()
        );
        assert_eq!(descriptor.participant.id, "binance");
        assert_eq!(descriptor.principal_id.as_deref(), Some("main"));
    }
    assert_eq!(account_read.descriptor(), earn.descriptor());
    assert_eq!(account_read.descriptor(), inspection.descriptor());
    assert_eq!(earn.descriptor(), transfer.descriptor());
    assert_eq!(
        account_read.descriptor(),
        blocking_account_read.descriptor()
    );
    assert_eq!(earn.descriptor(), blocking_earn.descriptor());
    assert_eq!(inspection.descriptor(), blocking_inspection.descriptor());
    assert_eq!(transfer.descriptor(), blocking_transfer.descriptor());
}

#[test]
fn provider_context_shares_ip_http_lane_but_separates_principals() {
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "live".into(),
        rest_base_url: "https://api.binance.com".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let main = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.main".into(),
            principal_id: Some("main".into()),
            api_key: SecretString::from("main-key"),
            secret: SecretString::from("main-secret"),
            principal_quota: None,
        })
        .unwrap();
    let hedge = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.hedge".into(),
            principal_id: Some("hedge".into()),
            api_key: SecretString::from("hedge-key"),
            secret: SecretString::from("hedge-secret"),
            principal_quota: None,
        })
        .unwrap();

    assert!(main.shares_provider_http_with(&hedge));
    let usdm = native_usdm_principal("https://fapi.binance.com".into(), "execution.binance.usdm");
    let options = native_options_principal(
        "https://eapi.binance.com".into(),
        "execution.binance.options",
    );
    assert_eq!(usdm.descriptor().domain.as_str(), "usd-m-futures");
    assert_eq!(options.descriptor().domain.as_str(), "options");
    assert_ne!(
        main.spot_descriptor().binding_id,
        hedge.spot_descriptor().binding_id
    );
    assert_ne!(
        main.spot_descriptor().principal_id,
        hedge.spot_descriptor().principal_id
    );
}

#[test]
fn spot_channel_validates_transport_without_network_access() {
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "live".into(),
        rest_base_url: "https://api.binance.com".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let connection = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.validation".into(),
            principal_id: Some("validation".into()),
            api_key: SecretString::from("key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let error = connection
        .spot_order_events(&super::BinanceSpotChannelConfig {
            websocket_api_url: "https://ws-api.binance.com".into(),
            event_queue_capacity: 8,
        })
        .err()
        .unwrap();
    assert!(matches!(
        error,
        crate::application::IntegrationError::InvalidRequest(_)
    ));
}

#[tokio::test(flavor = "current_thread")]
async fn instrument_catalog_uses_the_callers_runtime_and_trait_is_the_capability() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    fn is_async<T: AsyncInstrumentCatalogConnection>(_: &T) {}
    fn is_blocking<T: InstrumentCatalogConnection>(_: &T) {}

    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0_u8; 4096];
        let read = stream.read(&mut request).await.unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/exchangeInfo "));
        let body = r#"{"symbols":[{"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","status":"TRADING","baseAssetPrecision":6,"quoteAssetPrecision":2}]}"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "public".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let mut catalog = provider.instrument_catalog();
    let blocking = provider.blocking_instrument_catalog();
    is_async(&catalog);
    is_blocking(&blocking);
    assert_eq!(catalog.descriptor().domain.as_str(), "spot");
    assert!(catalog.descriptor().principal_id.is_none());

    let facts = catalog.fetch_instruments().await.unwrap();
    assert_eq!(facts.participant.id.as_str(), "binance");
    assert_eq!(facts.instruments[0].source_symbol, "BTCUSDT");
    server.await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn async_order_entry_and_query_use_the_callers_runtime() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        for index in 0..3 {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 8_192];
            let read = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..read]);
            let body = match index {
                0 => {
                    assert!(request.starts_with("GET /api/v3/time "));
                    serde_json::json!({
                        "serverTime": std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap()
                            .as_millis() as u64
                    })
                    .to_string()
                }
                1 => {
                    assert!(request.starts_with("POST /api/v3/order?"));
                    r#"{"orderId":123,"status":"NEW","executedQty":"0.00"}"#.into()
                }
                _ => {
                    assert!(request.starts_with("GET /api/v3/openOrders?"));
                    r#"[{"orderId":"123","clientOrderId":"order-async-1","symbol":"BTCUSDT","side":"BUY","type":"MARKET","status":"NEW","origQty":"0.25","executedQty":"0","price":"0","time":1000}]"#.into()
                }
            };
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nX-MBX-USED-WEIGHT-1M: {}\r\n\r\n{}",
                body.len(),
                index + 1,
                body
            )
            .unwrap();
        }
    });

    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "test".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let connection = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.async-http".into(),
            principal_id: Some("async-http".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let request = OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new("order-async-1").unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-usdt").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::Spot.into()),
            "BTCUSDT",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(25, 2),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    };
    let mut entry = connection.spot_order_entry().unwrap();
    let outcome = AsyncOrderEntryConnection::submit_order(&mut entry, &request)
        .await
        .unwrap();
    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Confirmed(_)
    ));

    let mut query = connection.spot_order_query().unwrap();
    let rows = AsyncOrderQueryConnection::open_orders(
        &mut query,
        &crate::application::ExternalOrderQuery {
            symbol: Some(kairos_domain_types::Symbol::new("BTCUSDT").unwrap()),
            ..Default::default()
        },
    )
    .await
    .unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].order_id, "123");
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn futures_submit_server_failure_is_indeterminate_and_not_retried() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /fapi/v1/time "));
        let body = format!(
            "{{\"serverTime\":{}}}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);
        let (mut stream, _) = listener.accept().unwrap();
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("POST /fapi/v1/order?"));
        let body = r#"{"code":-1000,"msg":"unknown provider failure"}"#;
        write!(
            stream,
            "HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.usdm.failure".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let futures = native_usdm_principal(
        format!("http://{address}"),
        "execution.binance.usdm.failure",
    );
    let request = OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new("order-futures-1").unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("usd-m-futures").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-usdt").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::UsdMFutures.into()),
            "BTCUSDT",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(1, 3),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    };
    let mut entry = futures.order_entry();
    let outcome = AsyncOrderEntryConnection::submit_order(&mut entry, &request)
        .await
        .unwrap();

    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Indeterminate(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn futures_submit_preflight_failure_is_proven_not_sent() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /fapi/v1/time "));
        let body = r#"{}"#;
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.usdm.preflight".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let futures = native_usdm_principal(
        format!("http://{address}"),
        "execution.binance.usdm.preflight",
    );
    let mut entry = futures.order_entry();
    let error = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &futures_order_request("order-futures-preflight"),
    )
    .await
    .unwrap_err();

    assert!(matches!(
        error,
        crate::application::IntegrationError::Unavailable(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn futures_submit_response_loss_is_indeterminate_and_not_retried() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /fapi/v1/time "));
        let body = format!(
            "{{\"serverTime\":{}}}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);

        let (mut stream, _) = listener.accept().unwrap();
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("POST /fapi/v1/order?"));
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.usdm.response-loss".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let futures = native_usdm_principal(
        format!("http://{address}"),
        "execution.binance.usdm.response-loss",
    );
    let mut entry = futures.order_entry();
    let outcome = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &futures_order_request("order-futures-response-loss"),
    )
    .await
    .unwrap();

    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Indeterminate(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn margin_submit_preflight_failure_is_proven_not_sent() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/time "));
        let body = r#"{}"#;
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.margin.preflight".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let margin = principal.cross_margin_connection();
    let mut entry = margin.order_entry().unwrap();
    let error = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &margin_order_request("order-margin-preflight"),
    )
    .await
    .unwrap_err();

    assert!(matches!(
        error,
        crate::application::IntegrationError::Unavailable(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn margin_submit_response_loss_is_indeterminate_and_not_retried() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /api/v3/time "));
        let body = format!(
            "{{\"serverTime\":{}}}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);

        let (mut stream, _) = listener.accept().unwrap();
        let read = stream.read(&mut request).unwrap();
        assert!(
            String::from_utf8_lossy(&request[..read]).starts_with("POST /sapi/v1/margin/order?")
        );
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.margin.response-loss".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let margin = principal.cross_margin_connection();
    let mut entry = margin.order_entry().unwrap();
    let outcome = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &margin_order_request("order-margin-response-loss"),
    )
    .await
    .unwrap();

    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Indeterminate(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn options_submit_server_failure_is_indeterminate_and_not_retried() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /eapi/v1/time "));
        let body = format!(
            "{{\"serverTime\":{}}}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);
        let (mut stream, _) = listener.accept().unwrap();
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("POST /eapi/v1/order?"));
        let body = r#"{"code":-1000,"msg":"unknown provider failure"}"#;
        write!(
            stream,
            "HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.options.failure".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let options = native_options_principal(
        format!("http://{address}"),
        "execution.binance.options.failure",
    );
    let request = OrderEntryRequest {
        order_id: kairos_domain_types::OrderId::new("order-options-1").unwrap(),
        intent_id: None,
        account_id: kairos_domain_types::AccountId::new("main").unwrap(),
        segment_key: kairos_domain_types::SegmentKey::new("options").unwrap(),
        instrument_id: kairos_domain_types::InstrumentId::new("instrument:btc-call").unwrap(),
        market_id: None,
        provider_instrument: ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ConnectionDomain::Options.into()),
            "BTC-260327-100000-C",
        )
        .unwrap(),
        side: OrderSide::Buy,
        quantity: DecimalValue::new(1, 2),
        order_type: OrderType::Market,
        limit_price: None,
        options: Default::default(),
    };
    let mut entry = options.order_entry();
    let outcome = AsyncOrderEntryConnection::submit_order(&mut entry, &request)
        .await
        .unwrap();

    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Indeterminate(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn options_submit_preflight_failure_is_proven_not_sent() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /eapi/v1/time "));
        let body = r#"{}"#;
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.options.preflight".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let options = native_options_principal(
        format!("http://{address}"),
        "execution.binance.options.preflight",
    );
    let mut entry = options.order_entry();
    let error = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &options_order_request("order-options-preflight"),
    )
    .await
    .unwrap_err();

    assert!(matches!(
        error,
        crate::application::IntegrationError::Unavailable(_)
    ));
    server.join().unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn options_submit_response_loss_is_indeterminate_and_not_retried() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
    let address = listener.local_addr().unwrap();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4_096];
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("GET /eapi/v1/time "));
        let body = format!(
            "{{\"serverTime\":{}}}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis()
        );
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )
        .unwrap();
        drop(stream);

        let (mut stream, _) = listener.accept().unwrap();
        let read = stream.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..read]).starts_with("POST /eapi/v1/order?"));
        drop(stream);
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(matches!(
            listener.accept(),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
        ));
    });
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: format!("http://{address}"),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let _principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.options.response-loss".into(),
            principal_id: Some("test".into()),
            api_key: SecretString::from("api-key"),
            secret: SecretString::from("secret"),
            principal_quota: None,
        })
        .unwrap();
    let options = native_options_principal(
        format!("http://{address}"),
        "execution.binance.options.response-loss",
    );
    let mut entry = options.order_entry();
    let outcome = AsyncOrderEntryConnection::submit_order(
        &mut entry,
        &options_order_request("order-options-response-loss"),
    )
    .await
    .unwrap();

    assert!(matches!(
        outcome,
        crate::application::CommandOutcome::Indeterminate(_)
    ));
    server.join().unwrap();
}
