use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderQueryConnection, ConnectionDescriptor, ConnectionHealth,
    ConnectionLifecycle, ConnectionState, ExternalAccountEvent, ExternalAccountSegment,
    ExternalAccountSnapshot, ExternalAccountStatus, ExternalDecimal, ExternalFillEvent,
    IntegrationError, ParticipantKind, ParticipantRef,
};
use kairos_integration::blocking::{
    AccountEventReceive, AccountEventStreamConnection, AccountReadConnection,
    BufferedIntegrationAccountStream, IntegrationAccountStream, OrderEntryConnection,
    OrderQueryConnection,
};
use kairos_integration::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, BinanceQuotaAllocation,
    ConnectionDomain,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig, OkxPrincipalConfig,
    OkxPrincipalConnection, OkxPrivateChannelConfig, TradingMode as OkxTradingMode,
};
use kairos_integration::participants::{binance, ibkr};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

fn okx_principal() -> OkxPrincipalConnection {
    OkxConnection::connect(OkxConnectionConfig {
        environment: "test".into(),
        rest_base_url: "http://127.0.0.1:1".into(),
        shared_quota: None,
    })
    .unwrap()
    .principal_connection(OkxPrincipalConfig {
        binding_id: "account.okx.test".into(),
        principal_id: Some("principal".into()),
        api_key: "okx-key".into(),
        secret: "okx-secret".into(),
        passphrase: "okx-passphrase".into(),
        quota: None,
        order_quota: None,
    })
    .unwrap()
}

struct FixtureConnection;

impl FixtureConnection {
    fn new() -> Self {
        ConnectionDescriptor::new(
            "account.fixture",
            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
            "account-read",
        )
        .unwrap();
        Self
    }
}

impl AccountReadConnection for FixtureConnection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        Ok(ExternalAccountSnapshot {
            segment_key: segment.segment_key.clone(),
            balances: Vec::new(),
            collateral: Vec::new(),
            positions: Vec::new(),
            open_orders: Vec::new(),
            status: ExternalAccountStatus::Ready,
            observed_at_unix_nanos: 0.into(),
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
        identity:
            kairos_integration::application::capabilities::account_facts::ExternalAccountIdentity {
                broker: "fixture".into(),
                account_id: kairos_domain_types::AccountId::new("main").unwrap(),
            },
        segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
        environment: "paper".into(),
        account_model: None,
    };
    let mut source = FixtureConnection::new();
    let snapshot = source.fetch_account(&segment).unwrap();
    assert_eq!(snapshot.segment_key, "spot");
}

struct ReconnectingAccountStream {
    state: ConnectionState,
    calls: usize,
    reconnects: Arc<AtomicUsize>,
}

impl ReconnectingAccountStream {
    fn new(reconnects: Arc<AtomicUsize>) -> Self {
        let identity = ConnectionDescriptor::new(
            "account.fixture.stream",
            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
            "account-stream",
        )
        .unwrap();
        Self {
            state: ConnectionState::new(identity),
            calls: 0,
            reconnects,
        }
    }
}

impl AccountEventStreamConnection for ReconnectingAccountStream {
    fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Ready;
        Ok(())
    }

    fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.reconnects.fetch_add(1, Ordering::SeqCst);
        self.connect_channel()
    }

    fn channel_health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: true,
            last_error: None,
        }
    }

    fn recv_account_event(
        &mut self,
        timeout: std::time::Duration,
    ) -> Result<AccountEventReceive, IntegrationError> {
        self.calls += 1;
        match self.calls {
            1 => Err(IntegrationError::Transport(
                "simulated account stream disconnect".into(),
            )),
            2 => Ok(AccountEventReceive::Event(ExternalAccountEvent::Fill(
                ExternalFillEvent {
                    fill_id: kairos_domain_types::FillId::new("account-recovered-fill").unwrap(),
                    order_id: kairos_domain_types::OrderId::new("local-order-1").unwrap(),
                    segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
                    provider_instrument:
                        kairos_integration::application::ProviderInstrumentRef::new(
                            ParticipantRef::new(ParticipantKind::Exchange, "fixture").unwrap(),
                            None,
                            "BTCUSDT",
                        )
                        .unwrap(),
                    side: "buy".into(),
                    quantity: ExternalDecimal {
                        mantissa: 1,
                        scale: 0,
                    },
                    price: ExternalDecimal {
                        mantissa: 100,
                        scale: 0,
                    },
                    fee_asset: None,
                    fee_amount: None,
                    occurred_at_unix_nanos: 42.into(),
                },
            ))),
            _ => {
                std::thread::sleep(timeout);
                Ok(AccountEventReceive::Idle)
            }
        }
    }
}

#[test]
fn buffered_account_stream_reconnects_after_read_failure() {
    let reconnects = Arc::new(AtomicUsize::new(0));
    let mut stream: BufferedIntegrationAccountStream =
        IntegrationAccountStream::new(ReconnectingAccountStream::new(Arc::clone(&reconnects)))
            .buffered();
    let wakeup = Arc::new(tokio::sync::Notify::new());
    stream.register_wakeup(Arc::clone(&wakeup));
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    let mut saw_error = false;
    let mut saw_fill = false;
    while std::time::Instant::now() < deadline && !saw_fill {
        runtime.block_on(async {
            tokio::time::timeout(
                deadline.saturating_duration_since(std::time::Instant::now()),
                wakeup.notified(),
            )
            .await
            .expect("account stream should wake the async runtime")
        });
        loop {
            match stream.next_event() {
                Err(_) => saw_error = true,
                Ok(Some(ExternalAccountEvent::Fill(fill))) => {
                    saw_fill = fill.fill_id == "account-recovered-fill";
                }
                Ok(Some(_)) => {}
                Ok(None) => break,
            }
        }
    }
    assert!(saw_error);
    assert!(saw_fill);
    assert_eq!(reconnects.load(Ordering::SeqCst), 1);
}

#[test]
fn integration_exposes_binance_spot_as_a_provider_native_connection() {
    let provider = BinanceConnection::connect(BinanceConnectionConfig {
        environment: "test".into(),
        rest_base_url: "http://127.0.0.1:1".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let connection = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.fixture".into(),
            principal_id: Some("fixture".into()),
            api_key: "api-key".into(),
            secret: "secret".into(),
            principal_quota: None,
        })
        .unwrap();
    connection.spot_order_entry().unwrap();
}

#[test]
fn integration_projects_binance_usdm_futures_async_entry_without_network_access() {
    let provider = BinanceConnection::connect(BinanceConnectionConfig {
        environment: "testnet".into(),
        rest_base_url: "http://127.0.0.1:1".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.usdm.fixture".into(),
            principal_id: Some("fixture".into()),
            api_key: "api-key".into(),
            secret: "secret".into(),
            principal_quota: None,
        })
        .unwrap();
    let futures = principal.usd_m_futures_connection().unwrap();
    fn binance_async_capability<T: AsyncOrderEntryConnection>(_: &T) {}
    binance_async_capability(&futures.order_entry());

    fn async_capability<T: AsyncOrderEntryConnection>(_: &T) {}
    fn blocking_capability<T: OrderEntryConnection>(_: &T) {}
    let principal = okx_principal();
    let entry = principal
        .trading_order_entry(OkxInstrumentType::Swap, OkxTradingMode::Cross)
        .unwrap();
    let blocking = principal
        .blocking_trading_order_entry(OkxInstrumentType::Swap, OkxTradingMode::Cross)
        .unwrap();
    async_capability(&entry);
    blocking_capability(&blocking);
}

#[test]
fn integration_composes_remote_order_queries_for_native_private_products() {
    fn async_capability<T: AsyncOrderQueryConnection>(_: &T) {}
    fn blocking_capability<T: OrderQueryConnection>(_: &T) {}
    let provider = BinanceConnection::connect(BinanceConnectionConfig {
        environment: "test".into(),
        rest_base_url: "http://127.0.0.1:1".into(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .unwrap();
    let binance_principal = provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: "execution.binance.options.test".into(),
            principal_id: Some("test".into()),
            api_key: "binance-key".into(),
            secret: "binance-secret".into(),
            principal_quota: None,
        })
        .unwrap();
    let options_query = binance_principal
        .options_connection()
        .unwrap()
        .order_query();
    async_capability(&options_query);
    assert!(matches!(
        binance::blocking::order_query(
            ConnectionDomain::Options,
            "binance-key",
            "binance-secret",
            "http://127.0.0.1:1",
        ),
        Err(IntegrationError::UnsupportedOperation)
    ));
    let principal = okx_principal();
    let query = principal.trading_order_query(OkxInstrumentType::Spot);
    let blocking = principal.blocking_trading_order_query(OkxInstrumentType::Spot);
    async_capability(&query);
    blocking_capability(&blocking);
}

#[test]
fn integration_composes_ibkr_equity_account_and_async_execution_without_network_access() {
    let config = ibkr::IbkrConnectionConfig {
        host: "127.0.0.1".into(),
        port: 4002,
        client_id: 0,
    };
    ibkr::blocking::account(&config).unwrap();
    ibkr::blocking::account_stream(&config, "DU123", "equity").unwrap();
    let connection = ibkr::IbkrConnection::connect(config, "ibkr.principal.test", "DU123").unwrap();
    fn async_entry<T: AsyncOrderEntryConnection>(_: &T) {}
    fn async_query<T: AsyncOrderQueryConnection>(_: &T) {}
    fn async_events<T: kairos_integration::application::AsyncOrderEventSource>(_: &T) {}
    async_entry(&connection.order_entry());
    async_query(&connection.order_query());
    let events = connection.order_events(Some("AAPL".into()));
    async_events(&events);
    assert_eq!(
        kairos_integration::application::AsyncOrderEventSource::channel_health(&events).lifecycle,
        kairos_integration::application::ConnectionLifecycle::Created
    );
}

#[test]
fn integration_composes_okx_options_account_and_order_entry_without_network_access() {
    let principal = okx_principal();
    let account = principal.trading_account(OkxInstrumentType::Option);
    assert_eq!(account.descriptor().participant.id, "okx");
    assert_eq!(account.descriptor().domain.as_str(), "trading");
    let order = principal
        .trading_order_entry(OkxInstrumentType::Option, OkxTradingMode::Cross)
        .unwrap();
    assert_eq!(order.descriptor().participant.id, "okx");
    assert_eq!(order.descriptor().domain.as_str(), "trading");
}

#[test]
fn integration_composes_binance_spot_market_profile_without_network_access() {
    binance::blocking::spot_market_profile("api-key", "secret", "http://127.0.0.1:1").unwrap();
}

#[test]
fn integration_composes_native_credential_inspection_without_network_access() {
    binance::blocking::spot_credential_inspection("api-key", "secret", "http://127.0.0.1:1")
        .unwrap();
    let inspection = okx_principal().trading_credential_inspection(OkxInstrumentType::Spot);
    assert_eq!(inspection.descriptor().participant.id, "okx");
}

#[test]
fn integration_composes_binance_margin_account_without_network_access() {
    for product in [
        ConnectionDomain::CrossMargin,
        ConnectionDomain::IsolatedMargin,
    ] {
        binance::blocking::margin_account(product, "api-key", "secret", "http://127.0.0.1:1")
            .unwrap();
    }
}

#[test]
fn integration_composes_okx_margin_account_without_network_access() {
    let connection = okx_principal().trading_account(OkxInstrumentType::Margin);
    assert_eq!(connection.descriptor().domain.as_str(), "trading");
    assert!(connection
        .descriptor()
        .binding_id
        .ends_with(".trading.margin"));
}

#[test]
fn integration_composes_okx_market_profile_without_network_access() {
    let profile = okx_principal().trading_account_market_profile(OkxInstrumentType::Swap);
    assert_eq!(profile.descriptor().participant.id, "okx");
    assert_eq!(profile.descriptor().domain.as_str(), "trading");
}

#[test]
fn integration_composes_binance_private_account_stream_without_network_access() {
    binance::blocking::spot_account_stream(
        "api-key",
        "secret",
        "http://127.0.0.1:1",
        "ws://127.0.0.1:1",
        "spot",
    )
    .unwrap();
}

#[test]
fn integration_composes_futures_and_okx_private_account_streams_without_network_access() {
    binance::blocking::futures_account_stream(
        ConnectionDomain::UsdMFutures,
        "binance-key",
        "binance-secret",
        "http://127.0.0.1:1",
        "ws://127.0.0.1:1",
        "usd_m_futures",
    )
    .unwrap();
    let events = okx_principal()
        .trading_account_events(
            OkxInstrumentType::Spot,
            "spot",
            &OkxPrivateChannelConfig {
                websocket_url: "ws://127.0.0.1:1".into(),
                event_queue_capacity: 8,
            },
        )
        .unwrap();
    assert_eq!(events.descriptor().participant.id, "okx");
}
