use kairos_integration::application::{AccountReadConnection, Connection};
use kairos_integration::domain::{
    AccessScope, ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionSpec,
    ConnectionState, ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus,
    IntegrationCapability, ProductFamily, TransportKind,
};

struct FixtureConnection {
    state: ConnectionState,
}

impl FixtureConnection {
    fn new() -> Self {
        let spec = ConnectionSpec {
            connection_id: "account.fixture".into(),
            route: kairos_integration::domain::IntegrationRoute::exchange("fixture"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("fixture".into()),
            asset_type: None,
        };
        let identity = ConnectionIdentity::new(
            spec.connection_id,
            spec.route,
            spec.product,
            spec.access,
            spec.transport,
            spec.capability,
        )
        .unwrap();
        Self {
            state: ConnectionState::new(identity),
        }
    }
}

impl Connection for FixtureConnection {
    fn identity(&self) -> &ConnectionIdentity {
        &self.state.identity
    }
    fn state(&self) -> &ConnectionState {
        &self.state
    }
    fn start(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.start()
    }
    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: true,
            authenticated: true,
            last_error: None,
        }
    }
}

impl AccountReadConnection for FixtureConnection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, kairos_integration::IntegrationError> {
        Ok(ExternalAccountSnapshot {
            segment_key: segment.segment_key.clone(),
            balances: Vec::new(),
            collateral: Vec::new(),
            positions: Vec::new(),
            open_orders: Vec::new(),
            status: ExternalAccountStatus::Ready,
            observed_at_unix_nanos: 0,
            equity: None,
            initial_equity: None,
            net_profit: None,
            account_model: None,
            margin_mode: None,
            position_mode: None,
            partial: false,
        })
    }
}

#[test]
fn integration_connection_exposes_only_normalized_external_facts() {
    let segment = ExternalAccountSegment {
        identity: kairos_integration::domain::account::ExternalAccountIdentity {
            broker: "fixture".into(),
            account_id: "main".into(),
        },
        segment_key: "spot".into(),
        environment: "paper".into(),
        account_model: None,
    };
    let mut source = FixtureConnection::new();
    let snapshot = source.fetch_account(&segment).unwrap();
    assert_eq!(snapshot.segment_key, "spot");
}

#[test]
fn integration_exposes_binance_order_entry_as_a_lifecycle_connection_capability() {
    let integration = kairos_integration::Integration::new().with_binance_spot_order_entry(
        "api-key",
        "secret",
        "http://127.0.0.1:1",
    );
    let spec = ConnectionSpec {
        connection_id: "order.fixture".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
        product: Some(ProductFamily::Spot),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::OrderEntry,
        credential_id: Some("fixture".into()),
        asset_type: None,
    };
    let connection = integration.connect_order_entry(&spec).unwrap();
    assert_eq!(connection.identity().route.primary().id, "binance");
    assert!(connection.identity().capability == IntegrationCapability::OrderEntry);
}

#[test]
fn integration_composes_binance_equity_order_entry_without_network_access() {
    let integration = kairos_integration::Integration::new().with_binance_equity_order_entry(
        "equity-key",
        "equity-secret",
        "http://127.0.0.1:1",
    );
    let connection = integration
        .connect_order_entry(&ConnectionSpec {
            connection_id: "execution.binance.equity".into(),
            route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Equity),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::OrderEntry,
            credential_id: Some("equity".into()),
            asset_type: Some(kairos_integration::domain::AssetType::Equity),
        })
        .unwrap();
    assert_eq!(connection.identity().product, Some(ProductFamily::Equity));
}

#[test]
fn integration_composes_binance_futures_and_okx_order_entries_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_binance_futures_order_entry(
            ProductFamily::UsdMFutures,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
        )
        .unwrap()
        .with_okx_order_entry(
            ProductFamily::UsdMFutures,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "http://127.0.0.1:1",
        )
        .unwrap();
    let spec = |provider: &str| ConnectionSpec {
        connection_id: format!("order.{provider}"),
        route: kairos_integration::domain::IntegrationRoute::exchange(provider),
        product: Some(ProductFamily::UsdMFutures),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::OrderEntry,
        credential_id: Some(provider.into()),
        asset_type: None,
    };
    assert_eq!(
        integration
            .connect_order_entry(&spec("binance"))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "binance"
    );
    assert_eq!(
        integration
            .connect_order_entry(&spec("okx"))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "okx"
    );
}

#[test]
fn integration_composes_remote_order_queries_for_native_private_products() {
    let integration = kairos_integration::Integration::new()
        .with_binance_order_query(
            ProductFamily::Spot,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
        )
        .with_binance_order_query(
            ProductFamily::UsdMFutures,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
        )
        .with_binance_order_query(
            ProductFamily::Options,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
        )
        .with_okx_order_query(
            ProductFamily::Spot,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "http://127.0.0.1:1",
        );
    let spec = |provider: &str, product: ProductFamily| ConnectionSpec {
        connection_id: format!("query.{provider}.{product:?}"),
        route: kairos_integration::domain::IntegrationRoute::exchange(provider),
        product: Some(product),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::OrderRead,
        credential_id: Some(provider.into()),
        asset_type: None,
    };
    for (provider, product) in [
        ("binance", ProductFamily::Spot),
        ("binance", ProductFamily::UsdMFutures),
        ("binance", ProductFamily::Options),
        ("okx", ProductFamily::Spot),
    ] {
        let connection = integration
            .connect_order_query(&spec(provider, product))
            .unwrap();
        assert_eq!(
            connection.identity().capability,
            IntegrationCapability::OrderRead
        );
    }
}

#[test]
fn integration_composes_ibkr_equity_account_order_and_stream_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_ibkr_account("127.0.0.1", 4002, 0)
        .with_ibkr_order_entry("127.0.0.1", 4002, 0)
        .with_ibkr_account_stream("127.0.0.1", 4002, 0, "DU123", "equity");
    let spec = |capability| ConnectionSpec {
        connection_id: format!("ibkr.{capability:?}"),
        route: kairos_integration::domain::IntegrationRoute::broker("ibkr"),
        product: Some(ProductFamily::Spot),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability,
        credential_id: Some("ibkr".into()),
        asset_type: Some(kairos_integration::domain::AssetType::Equity),
    };
    assert_eq!(
        integration
            .connect_account(&spec(IntegrationCapability::AccountRead))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "ibkr"
    );
    assert_eq!(
        integration
            .connect_order_entry(&spec(IntegrationCapability::OrderEntry))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "ibkr"
    );
    assert_eq!(
        integration
            .connect_account_stream(&spec(IntegrationCapability::AccountStream))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "ibkr"
    );
}

#[test]
fn integration_composes_ibkr_execution_stream_without_network_access() {
    let integration = kairos_integration::Integration::new().with_ibkr_execution_stream(
        "127.0.0.1",
        4002,
        0,
        "DU123",
        Some("AAPL".into()),
    );
    let connection = integration
        .connect_execution_stream(&ConnectionSpec {
            connection_id: "execution.ibkr.equity.stream".into(),
            route: kairos_integration::domain::IntegrationRoute::broker("ibkr"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::ExecutionStream,
            credential_id: Some("ibkr".into()),
            asset_type: Some(kairos_integration::domain::AssetType::Equity),
        })
        .unwrap();
    assert_eq!(
        connection.identity().capability,
        IntegrationCapability::ExecutionStream
    );
}

#[test]
fn integration_composes_okx_options_account_and_order_entry_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_okx_account(
            ProductFamily::Options,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "http://127.0.0.1:1",
        )
        .unwrap()
        .with_okx_order_entry(
            ProductFamily::Options,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "http://127.0.0.1:1",
        )
        .unwrap();
    let account_spec = ConnectionSpec {
        connection_id: "account.okx.options".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("okx"),
        product: Some(ProductFamily::Options),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::AccountRead,
        credential_id: Some("okx".into()),
        asset_type: None,
    };
    let order_spec = ConnectionSpec {
        capability: IntegrationCapability::OrderEntry,
        transport: TransportKind::Rest,
        connection_id: "order.okx.options".into(),
        ..account_spec.clone()
    };
    assert_eq!(
        integration
            .connect_account(&account_spec)
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "okx"
    );
    assert_eq!(
        integration
            .connect_order_entry(&order_spec)
            .unwrap()
            .identity()
            .product,
        Some(ProductFamily::Options)
    );
}

#[test]
fn integration_composes_binance_spot_market_profile_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_binance_spot_account_market_profile("api-key", "secret", "http://127.0.0.1:1");
    let spec = ConnectionSpec {
        connection_id: "account.binance.spot.market-profile".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
        product: Some(ProductFamily::Spot),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::AccountMarketProfileRead,
        credential_id: Some("binance".into()),
        asset_type: None,
    };
    assert_eq!(
        integration
            .connect_account_market_profile(&spec)
            .unwrap()
            .identity()
            .capability,
        IntegrationCapability::AccountMarketProfileRead
    );
}

#[test]
fn integration_composes_native_credential_inspection_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_binance_spot_account("api-key", "secret", "http://127.0.0.1:1")
        .with_okx_account(
            ProductFamily::Spot,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "http://127.0.0.1:1",
        )
        .unwrap();
    for (provider, credential_id) in [("binance", "binance"), ("okx", "okx")] {
        let connection = integration
            .connect_account_credential_inspection(&ConnectionSpec {
                connection_id: format!("account.{provider}.inspect"),
                route: kairos_integration::domain::IntegrationRoute::exchange(provider),
                product: Some(ProductFamily::Spot),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountCredentialInspection,
                credential_id: Some(credential_id.into()),
                asset_type: None,
            })
            .unwrap();
        assert_eq!(connection.identity().route.primary().id, provider);
    }
}

#[test]
fn integration_composes_binance_margin_account_without_network_access() {
    for product in [ProductFamily::CrossMargin, ProductFamily::IsolatedMargin] {
        let integration = kairos_integration::Integration::new()
            .with_binance_margin_account(product, "api-key", "secret", "http://127.0.0.1:1")
            .unwrap();
        let connection = integration
            .connect_account(&ConnectionSpec {
                connection_id: "account.binance.margin".into(),
                route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountRead,
                credential_id: Some("binance".into()),
                asset_type: None,
            })
            .unwrap();
        assert_eq!(connection.identity().product, Some(product));
    }
}

#[test]
fn integration_composes_okx_margin_account_without_network_access() {
    for product in [ProductFamily::CrossMargin, ProductFamily::IsolatedMargin] {
        let integration = kairos_integration::Integration::new()
            .with_okx_account(
                product,
                "okx-key",
                "okx-secret",
                "okx-passphrase",
                "http://127.0.0.1:1",
            )
            .unwrap();
        let connection = integration
            .connect_account(&ConnectionSpec {
                connection_id: "account.okx.margin".into(),
                route: kairos_integration::domain::IntegrationRoute::exchange("okx"),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountRead,
                credential_id: Some("okx".into()),
                asset_type: None,
            })
            .unwrap();
        assert_eq!(connection.identity().product, Some(product));
    }
}

#[test]
fn integration_composes_okx_market_profile_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_okx_account_market_profile(
            ProductFamily::UsdMFutures,
            "api-key",
            "secret",
            "passphrase",
            "http://127.0.0.1:1",
        )
        .unwrap();
    let spec = ConnectionSpec {
        connection_id: "account.okx.swap.market-profile".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("okx"),
        product: Some(ProductFamily::UsdMFutures),
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::AccountMarketProfileRead,
        credential_id: Some("okx".into()),
        asset_type: None,
    };
    assert_eq!(
        integration
            .connect_account_market_profile(&spec)
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "okx"
    );
}

#[test]
fn integration_composes_binance_private_account_stream_without_network_access() {
    let integration = kairos_integration::Integration::new().with_binance_spot_account_stream(
        "api-key",
        "secret",
        "http://127.0.0.1:1",
        "ws://127.0.0.1:1",
        "spot",
    );
    let spec = ConnectionSpec {
        connection_id: "account-stream.fixture".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
        product: Some(ProductFamily::Spot),
        access: AccessScope::Private,
        transport: TransportKind::UserStream,
        capability: IntegrationCapability::AccountStream,
        credential_id: Some("fixture".into()),
        asset_type: None,
    };
    let connection = integration.connect_account_stream(&spec).unwrap();
    assert_eq!(
        connection.identity().capability,
        IntegrationCapability::AccountStream
    );
}

#[test]
fn integration_composes_binance_transfer_as_a_connection_capability() {
    let integration = kairos_integration::Integration::new().with_binance_transfer(
        "api-key",
        "secret",
        "http://127.0.0.1:1",
    );
    let spec = ConnectionSpec {
        connection_id: "transfer.fixture".into(),
        route: kairos_integration::domain::IntegrationRoute::exchange("binance"),
        product: None,
        access: AccessScope::Private,
        transport: TransportKind::Rest,
        capability: IntegrationCapability::Transfer,
        credential_id: Some("fixture".into()),
        asset_type: None,
    };
    let connection = integration.connect_transfer(&spec).unwrap();
    assert_eq!(
        connection.identity().capability,
        IntegrationCapability::Transfer
    );
}

#[test]
fn integration_composes_futures_and_okx_private_account_streams_without_network_access() {
    let integration = kairos_integration::Integration::new()
        .with_binance_futures_account_stream(
            ProductFamily::UsdMFutures,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
            "ws://127.0.0.1:1",
            "usd_m_futures",
        )
        .unwrap()
        .with_okx_account_stream(
            ProductFamily::Spot,
            "okx-key",
            "okx-secret",
            "okx-passphrase",
            "ws://127.0.0.1:1",
            "spot",
        )
        .unwrap();
    let spec = |provider: &str, product: ProductFamily| ConnectionSpec {
        connection_id: format!("stream.{provider}"),
        route: kairos_integration::domain::IntegrationRoute::exchange(provider),
        product: Some(product),
        access: AccessScope::Private,
        transport: TransportKind::UserStream,
        capability: IntegrationCapability::AccountStream,
        credential_id: Some(provider.into()),
        asset_type: None,
    };
    assert_eq!(
        integration
            .connect_account_stream(&spec("binance", ProductFamily::UsdMFutures))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "binance"
    );
    assert_eq!(
        integration
            .connect_account_stream(&spec("okx", ProductFamily::Spot))
            .unwrap()
            .identity()
            .route
            .primary()
            .id,
        "okx"
    );
}
