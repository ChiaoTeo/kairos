use kairos_integration::participants::{hyperliquid, ibkr, okx};

#[test]
fn okx_connections_are_separate_concrete_transport_owners() {
    let public = okx::public::OkxPublicRestConnection::new(
        kairos_integration::ConnectionKey::new("okx.public").unwrap(),
        okx::OkxRestConfig {
            environment: "test".into(),
            endpoint: "https://www.okx.com".into(),
        },
    )
    .unwrap();
    let private = okx::private::OkxPrivateRestConnection::new(
        kairos_integration::ConnectionKey::new("okx.private").unwrap(),
        okx::OkxPrivateRestConfig {
            connection: okx::OkxRestConfig {
                environment: "test".into(),
                endpoint: "https://www.okx.com".into(),
            },
            credential: okx::OkxCredential {
                principal_id: "main".into(),
                api_key: "key".into(),
                secret: "secret".into(),
                passphrase: "passphrase".into(),
            },
        },
    )
    .unwrap();
    assert_eq!(public.descriptor().domain.as_str(), "public.rest");
    assert_eq!(private.descriptor().domain.as_str(), "private.rest");
    assert_eq!(private.descriptor().principal_id.as_deref(), Some("main"));
}

#[test]
fn hyperliquid_keeps_info_exchange_and_unified_websocket_distinct() {
    let info = hyperliquid::info::HyperliquidInfoRestConnection::new(
        kairos_integration::ConnectionKey::new("hyperliquid.info").unwrap(),
        hyperliquid::HyperliquidRestConfig {
            environment: "test".into(),
            endpoint: "https://api.hyperliquid.xyz/info".into(),
        },
    )
    .unwrap();
    let websocket = hyperliquid::HyperliquidWebSocketConnection::new(
        kairos_integration::ConnectionKey::new("hyperliquid.websocket").unwrap(),
        hyperliquid::HyperliquidWebSocketConfig {
            environment: "test".into(),
            endpoint: "wss://api.hyperliquid.xyz/ws".into(),
            event_capacity: 64,
            user: None,
        },
    )
    .unwrap();
    assert_eq!(info.descriptor().domain.as_str(), "info.rest");
    assert_eq!(websocket.descriptor().domain.as_str(), "websocket");
}

#[test]
fn ibkr_exposes_separate_virtual_connections() {
    let account = ibkr::IbkrAccountStreamConnection::new(
        kairos_integration::ConnectionKey::new("ibkr.account.main").unwrap(),
        ibkr::IbkrAccountStreamConfig {
            environment: "paper".into(),
            host: "127.0.0.1".into(),
            port: 7497,
            client_id: 7,
            account_id: "DU123".into(),
            segment_key: "equity".into(),
        },
    )
    .unwrap();
    let market = ibkr::IbkrMarketDataConnection::new(
        kairos_integration::ConnectionKey::new("ibkr.market.main").unwrap(),
        ibkr::IbkrMarketDataConfig {
            environment: "paper".into(),
            host: "127.0.0.1".into(),
            port: 7497,
            client_id: 8,
            exchange: "SMART".into(),
            currency: "USD".into(),
        },
    )
    .unwrap();
    assert_eq!(account.descriptor().domain.as_str(), "account.stream");
    assert_eq!(market.descriptor().domain.as_str(), "market-data");
    assert_eq!(
        account.descriptor().principal_id.as_deref(),
        Some("client-id:7")
    );
}
