// Connection composition tests live outside the production module boundary.

mod secret_tests {
    use super::super::model::{candidate_for_address, provider_instrument_for_route};
    use super::super::{compose_direct_execution_connections, ExecutionConnectionOptions};
    use crate::composition::{compose_execution_connections, compose_execution_routes};
    use kairos_integration::application::ParticipantKind;

    fn binance_spot_options() -> ExecutionConnectionOptions {
        ExecutionConnectionOptions {
            route_id: "binance.spot".into(),
            required: true,
            account_id: "main".into(),
            segment_key: "spot".into(),
            participant_id: "binance".into(),
            product: "spot".into(),
            trading_mode: None,
            api_key: "api-key-secret".into(),
            secret: "api-secret".into(),
            passphrase: "passphrase-secret".into(),
            base_url: "https://testnet.binance.vision".into(),
            websocket_url: "wss://ws-api.testnet.binance.vision/ws-api/v3".into(),
            isolated_symbol: None,
            instruments: Vec::new(),
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
            order_event_queue_capacity: 1_024,
            shared_quota_ledger_path: None,
            egress_scope_id: "test-egress".into(),
            principal_scope_id: "test-principal".into(),
            orders_per_10_seconds: 50,
            orders_per_day: 160_000,
            host: "127.0.0.1".into(),
            port: 4002,
            client_id: 0,
        }
    }

    #[test]
    fn execution_connection_debug_redacts_credentials() {
        let options = binance_spot_options();
        let output = format!("{options:?}");
        assert!(!output.contains("api-key-secret"));
        assert!(!output.contains("api-secret"));
        assert!(!output.contains("passphrase-secret"));
    }

    #[test]
    fn execution_route_maps_configured_provider_address() {
        let provider_instrument =
            provider_instrument_for_route("okx", "margin", "BTC-USDT").unwrap();
        assert_eq!(provider_instrument.participant.id, "okx");
        assert_eq!(
            provider_instrument
                .instrument_type
                .as_ref()
                .unwrap()
                .as_str(),
            "margin"
        );
        assert_eq!(provider_instrument.source_symbol.as_str(), "BTC-USDT");

        assert!(provider_instrument_for_route("future-provider", "margin", "BTC-USDT").is_err());
    }

    #[test]
    fn binance_equity_address_is_an_instrument_broker_route_without_a_fake_market() {
        let mut options = binance_spot_options();
        options.route_id = "binance.equity".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();

        let (candidate, provider_instrument) =
            candidate_for_address(&options, "instrument:equity:US:AAPL:common", None, "AAPL")
                .unwrap();

        assert!(candidate.market_id.is_none());
        assert_eq!(
            candidate.instrument_id.unwrap().as_str(),
            "instrument:equity:US:AAPL:common"
        );
        assert_eq!(
            provider_instrument.participant.kind,
            ParticipantKind::Broker
        );
        assert_eq!(provider_instrument.participant.id.as_str(), "binance");
    }

    #[test]
    fn binance_spot_route_uses_one_native_provider_context() {
        let connections = compose_execution_connections(&binance_spot_options()).unwrap();
        let descriptor = connections.descriptor.expect("native route descriptor");

        assert_eq!(descriptor.binding_id, "binance.principal.test-principal");
        assert_eq!(descriptor.environment, "testnet");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert!(connections.execution_stream.is_none());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn okx_route_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "okx.swap".into();
        options.participant_id = "okx".into();
        options.product = "swap".into();
        options.trading_mode = Some("cross".into());
        options.segment_key = "swap".into();
        options.base_url = "https://www.okx.com".into();
        let connections = compose_execution_connections(&options).unwrap();
        let descriptor = connections.descriptor.expect("native route descriptor");

        assert_eq!(
            descriptor.binding_id,
            "okx.principal.test-principal.trading.swap.cross"
        );
        assert_eq!(descriptor.domain.as_str(), "trading");
        assert_eq!(descriptor.participant.id.as_str(), "okx");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn okx_rejects_binance_settlement_product_names() {
        for product in ["usd-m-futures", "coin-m-futures"] {
            let mut options = binance_spot_options();
            options.participant_id = "okx".into();
            options.product = product.into();
            options.base_url = "https://www.okx.com".into();

            let error = compose_execution_routes(&[options])
                .err()
                .expect("a Binance settlement product must not select an OKX route");
            assert!(error.contains("unsupported OKX execution product"));
        }
    }

    #[test]
    fn okx_keeps_product_and_trading_mode_independent() {
        let mut options = binance_spot_options();
        options.participant_id = "okx".into();
        options.product = "margin".into();
        options.base_url = "https://www.okx.com".into();

        let error = compose_execution_routes(&[options.clone()])
            .err()
            .expect("margin without an explicit mode must fail closed");
        assert!(error.contains("requires explicit trading_mode"));

        options.trading_mode = Some("isolated".into());
        let connections = compose_execution_routes(&[options]).unwrap();
        assert!(connections.descriptors[0]
            .binding_id
            .ends_with("trading.margin.isolated"));
    }

    #[test]
    fn binance_rejects_okx_contract_product_names() {
        for product in ["swap", "futures"] {
            let mut options = binance_spot_options();
            options.product = product.into();

            let error = compose_execution_routes(&[options])
                .err()
                .expect("an OKX contract product must not select a Binance route");
            assert!(error.contains("production async execution route is not available"));
        }
    }

    #[test]
    fn ibkr_production_route_projects_only_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "ibkr.equity".into();
        options.participant_id = "ibkr".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();
        options.principal_scope_id = "tws-client-0".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("IBKR descriptor");

        assert_eq!(descriptor.binding_id, "ibkr.principal.tws-client-0");
        assert_eq!(descriptor.participant.id.as_str(), "ibkr");
        assert_eq!(descriptor.principal_id.as_deref(), Some("client-id:0"));
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.execution_stream.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_usd_m_futures_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.usdm".into();
        options.participant_id = "binance".into();
        options.product = "usd-m-futures".into();
        options.segment_key = "futures".into();
        options.base_url = "https://testnet.binancefuture.com".into();
        options.websocket_url = "wss://stream.binancefuture.com".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("futures descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.usd-m-futures"
        );
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.execution_stream.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_coin_m_futures_keeps_a_distinct_async_product_route() {
        let mut options = binance_spot_options();
        options.route_id = "binance.coinm".into();
        options.participant_id = "binance".into();
        options.product = "coin-m-futures".into();
        options.segment_key = "coin-m-futures".into();
        options.base_url = "https://testnet.binancefuture.com".into();
        options.websocket_url = "wss://dstream.binancefuture.com".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("COIN-M descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.coin-m-futures"
        );
        assert_eq!(descriptor.domain.as_str(), "coin-m-futures");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_cross_margin_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.cross-margin".into();
        options.product = "cross-margin".into();
        options.segment_key = "cross-margin".into();
        options.websocket_url = "wss://stream.binance.com:9443".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("margin descriptor");

        assert_eq!(
            descriptor.binding_id,
            "binance.principal.test-principal.cross-margin"
        );
        assert_eq!(descriptor.domain.as_str(), "cross-margin");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn binance_isolated_margin_requires_and_scopes_the_provider_symbol() {
        let mut options = binance_spot_options();
        options.route_id = "binance.isolated-margin.btcusdt".into();
        options.product = "isolated-margin".into();
        options.segment_key = "isolated-margin-btcusdt".into();

        let error = compose_execution_routes(&[options.clone()])
            .err()
            .expect("isolated route without symbol must fail");
        assert!(error.contains("isolated_symbol"));

        options.isolated_symbol = Some("btcusdt".into());
        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("isolated descriptor");

        assert_eq!(descriptor.domain.as_str(), "isolated-margin");
        assert!(connections.order_entry.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn production_rejects_unmigrated_live_blocking_provider_slice() {
        let mut options = binance_spot_options();
        options.route_id = "binance.equity".into();
        options.participant_id = "binance".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();

        let error = compose_execution_routes(&[options])
            .err()
            .expect("unmigrated live route must fail");

        assert!(error.contains("production async execution route is not available"));
        assert!(error.contains("equity"));
    }

    #[test]
    fn binance_options_projects_native_async_capabilities() {
        let mut options = binance_spot_options();
        options.route_id = "binance.options".into();
        options.product = "options".into();
        options.segment_key = "options".into();
        options.websocket_url = "wss://nbstream.binance.com/eoptions/private/stream".into();

        let connections = compose_execution_routes(&[options]).unwrap();
        let descriptor = connections.descriptor.expect("options descriptor");

        assert_eq!(descriptor.domain.as_str(), "options");
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
        assert_eq!(connections.async_execution_streams.len(), 1);
    }

    #[test]
    fn multi_route_composes_distinct_ibkr_client_sessions() {
        let mut first = binance_spot_options();
        first.route_id = "ibkr-main".into();
        first.participant_id = "ibkr".into();
        first.product = "equity".into();
        first.segment_key = "equity-main".into();
        first.account_id = "DU111".into();
        first.principal_scope_id = "ibkr-client-11".into();
        first.client_id = 11;
        let mut second = first.clone();
        second.route_id = "ibkr-secondary".into();
        second.segment_key = "equity-secondary".into();
        second.account_id = "DU222".into();
        second.principal_scope_id = "ibkr-client-12".into();
        second.client_id = 12;

        let connections = compose_execution_routes(&[first, second]).unwrap();

        assert_eq!(connections.descriptors.len(), 2);
        assert_eq!(connections.async_execution_streams.len(), 2);
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
    }

    #[test]
    fn multi_route_rejects_duplicate_ibkr_client_identity() {
        let mut first = binance_spot_options();
        first.route_id = "ibkr-main".into();
        first.participant_id = "ibkr".into();
        first.product = "equity".into();
        first.segment_key = "equity-main".into();
        first.account_id = "DU111".into();
        first.client_id = 11;
        let mut second = first.clone();
        second.route_id = "ibkr-secondary".into();
        second.segment_key = "equity-secondary".into();
        second.account_id = "DU222".into();

        let error = compose_execution_routes(&[first, second])
            .err()
            .expect("duplicate IBKR client identity must fail");

        assert!(error.contains("distinct TWS client id"));
    }

    #[tokio::test]
    async fn ibkr_direct_cli_uses_async_gateway_proxies() {
        let mut options = binance_spot_options();
        options.route_id = "ibkr-direct".into();
        options.participant_id = "ibkr".into();
        options.product = "equity".into();
        options.segment_key = "equity".into();

        let direct = compose_direct_execution_connections(&options).unwrap();
        let (entry, query, events, runtime) = direct.into_parts();

        assert!(entry.is_some());
        assert!(query.is_some());
        assert!(events.is_some());
        drop(runtime);
    }

    #[test]
    fn one_execution_process_composes_binance_and_okx_routes() {
        let binance = binance_spot_options();
        let mut okx = binance_spot_options();
        okx.route_id = "okx.swap".into();
        okx.participant_id = "okx".into();
        okx.product = "swap".into();
        okx.trading_mode = Some("cross".into());
        okx.segment_key = "swap".into();
        okx.base_url = "https://www.okx.com".into();
        okx.websocket_url = "wss://ws.okx.com:8443/ws/v5/private".into();
        okx.principal_scope_id = "okx-principal".into();

        let connections = compose_execution_routes(&[binance, okx]).unwrap();
        assert_eq!(connections.descriptors.len(), 2);
        assert!(connections.order_entry.is_none());
        assert!(connections.order_query.is_none());
        assert_eq!(connections.async_execution_streams.len(), 2);
        assert!(connections.descriptors.iter().any(|descriptor| descriptor
            .participant
            .id
            .as_str()
            == "binance"));
        assert!(connections.descriptors.iter().any(|descriptor| descriptor
            .participant
            .id
            .as_str()
            == "okx"));
        assert!(connections.async_order_entry.is_some());
        assert!(connections.async_order_query.is_some());
    }

    #[test]
    fn production_route_composition_does_not_construct_blocking_capabilities() {
        let source = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("src/composition/connections/routes.rs"),
        )
        .unwrap();
        let production = source
            .split("pub fn compose_execution_routes")
            .nth(1)
            .and_then(|source| source.split("pub fn compose_execution_connections").next())
            .expect("production route composition source");

        assert!(!production.contains(".blocking_"));
        assert!(!production.contains("kairos_integration::blocking"));
        assert!(!production.contains("ExecutionBlocking"));
    }
}
