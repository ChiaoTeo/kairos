//! Explicit one-shot diagnostic source composition.

mod manual {
    //! Process composition for concrete provider feeds.

    use crate::domain::source::{SourceDescriptor, SourceId};
    use crate::services::source::{spawn_snapshot, spawn_stream};
    use crate::MarketApplication;
    use kairos_integration::participants::hyperliquid::{
        info::HyperliquidInfoRestConnection, HyperliquidRestConfig, HyperliquidWebSocketConfig,
        HyperliquidWebSocketConnection,
    };
    use kairos_integration::participants::massive::{
        MassiveOptionsWebSocketConnection, MassiveStocksWebSocketConnection, MassiveWebSocketConfig,
    };
    use kairos_integration::participants::okx::{
        public::{OkxPublicRestConnection, OkxPublicWebSocketConnection},
        OkxRestConfig, OkxWebSocketConfig,
    };

    /// Market-owned source routing classification. Provider adapters map this to
    /// their own native vocabulary at composition time.
    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub enum MarketProduct {
        Spot,
        UsdMFutures,
        CoinMFutures,
        Options,
        Equity,
    }

    /// Canonical endpoint defaults shared by the one-shot CLI and Market server.
    pub fn default_endpoint(provider: &str) -> &'static str {
        match provider {
            "binance-spot-rest" => "https://api.binance.com",
            "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
            "binance-equity" => "https://api.binance.com",
            "binance-usdm-futures-websocket" => "wss://fstream.binance.com/ws",
            "binance-coinm-futures-websocket" => "wss://dstream.binance.com/ws",
            "binance-usdm-futures-rest" => "https://fapi.binance.com",
            "binance-coinm-futures-rest" => "https://dapi.binance.com",
            "binance-options-rest" => "https://eapi.binance.com",
            // Binance Options market streams are served by the futures stream
            // gateway. The Options-specific host currently returns 404.
            "binance-options-websocket" => "wss://fstream.binance.com/ws",
            "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
                "https://www.okx.com"
            }
            "okx-public-websocket" => "wss://ws.okx.com:8443/ws/v5/public",
            "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
            "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
            "hyperliquid-info" => "https://api.hyperliquid.xyz/info",
            "hyperliquid-websocket" => "wss://api.hyperliquid.xyz/ws",
            _ => "",
        }
    }

    #[cfg(test)]
    #[allow(clippy::items_after_test_module)]
    mod tests {
        use super::default_endpoint;

        #[test]
        fn binance_spot_websocket_uses_a_websocket_endpoint() {
            assert_eq!(
                default_endpoint("binance-spot-websocket"),
                "wss://stream.binance.com:9443/ws"
            );
        }

        #[test]
        fn unknown_endpoint_key_never_falls_back_to_binance() {
            assert_eq!(default_endpoint("future-provider"), "");
        }
    }

    pub(crate) fn attach_stream<
        C: kairos_integration::ConnectionLifecycleCommand
            + kairos_integration::MarketSubscriptionCommand
            + kairos_integration::MarketDataStream
            + 'static,
    >(
        runtime: &mut MarketApplication,
        source_id: &str,
        exchange: &str,
        market_type: &str,
        asset_type: &str,
        capabilities: impl IntoIterator<Item = crate::ObservationKind>,
        connection: C,
    ) -> Result<(), String> {
        let descriptor = SourceDescriptor::new(
            SourceId::new(source_id)?,
            kairos_primitives::Exchange::new(exchange).map_err(|error| error.to_string())?,
            market_type,
            Some(asset_type.into()),
        )?
        .with_observation_capabilities(capabilities);
        let input_capacity = runtime.source_input_capacity();
        runtime.attach_source(spawn_stream(descriptor, connection, input_capacity))
    }

    pub(crate) fn attach_binance_snapshot<C>(
        runtime: &mut MarketApplication,
        source_id: &str,
        market_type: &str,
        asset_type: &str,
        connection: C,
        interval: std::time::Duration,
    ) -> Result<(), String>
    where
        C: kairos_integration::MarketQuoteQuery + 'static,
    {
        let descriptor = SourceDescriptor::new(
            SourceId::new(source_id)?,
            kairos_primitives::Exchange::new("binance").map_err(|error| error.to_string())?,
            market_type,
            Some(asset_type.into()),
        )?
        .with_observation_capabilities(if market_type.eq_ignore_ascii_case("options") {
            vec![
                crate::ObservationKind::Quote,
                crate::ObservationKind::OptionGreeks,
            ]
        } else {
            vec![crate::ObservationKind::Quote]
        });
        let input_capacity = runtime.source_input_capacity();
        runtime.attach_source(spawn_snapshot(
            descriptor,
            connection,
            interval,
            input_capacity,
        ))
    }

    pub fn attach_okx_snapshot_source(
        runtime: &mut MarketApplication,
        source_id: &str,
        market_type: &str,
        asset_type: &str,
        endpoint: impl Into<String>,
        interval: std::time::Duration,
    ) -> Result<(), String> {
        let provider = OkxPublicRestConnection::new(OkxRestConfig {
            binding_id: source_id.into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
        })
        .map_err(|error| error.to_string())?;
        let descriptor = SourceDescriptor::new(
            SourceId::new(source_id)?,
            kairos_primitives::Exchange::new("okx").map_err(|error| error.to_string())?,
            market_type,
            Some(asset_type.into()),
        )?
        .with_observation_capabilities([crate::ObservationKind::Quote]);
        let handle = spawn_snapshot(
            descriptor,
            provider,
            interval,
            runtime.source_input_capacity(),
        );
        runtime.attach_source(handle)
    }
    pub fn attach_okx_live_source(
        runtime: &mut MarketApplication,
        source_id: &str,
        market_type: &str,
        endpoint: impl Into<String>,
    ) -> Result<(), String> {
        let provider = OkxPublicWebSocketConnection::new(OkxWebSocketConfig {
            binding_id: source_id.into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
            event_capacity: 4_096,
        })
        .map_err(|error| error.to_string())?;
        attach_stream(
            runtime,
            source_id,
            "okx",
            market_type,
            "crypto",
            [
                crate::ObservationKind::Trade,
                crate::ObservationKind::OrderBook,
            ],
            provider,
        )
    }

    pub fn attach_hyperliquid_snapshot_source(
        runtime: &mut MarketApplication,
        source_id: &str,
        market_type: &str,
        endpoint: impl Into<String>,
        interval: std::time::Duration,
    ) -> Result<(), String> {
        let provider = HyperliquidInfoRestConnection::new(HyperliquidRestConfig {
            binding_id: source_id.into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
        })
        .map_err(|error| error.to_string())?;
        let descriptor = SourceDescriptor::new(
            SourceId::new(source_id)?,
            kairos_primitives::Exchange::new("hyperliquid").map_err(|error| error.to_string())?,
            market_type,
            Some("crypto".into()),
        )?
        .with_observation_capabilities([crate::ObservationKind::Quote]);
        let handle = spawn_snapshot(
            descriptor,
            provider,
            interval,
            runtime.source_input_capacity(),
        );
        runtime.attach_source(handle)
    }

    pub fn attach_hyperliquid_live_source(
        runtime: &mut MarketApplication,
        source_id: &str,
        market_type: &str,
        endpoint: impl Into<String>,
    ) -> Result<(), String> {
        let provider = HyperliquidWebSocketConnection::new(HyperliquidWebSocketConfig {
            binding_id: source_id.into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
            event_capacity: 4_096,
            user: None,
        })
        .map_err(|error| error.to_string())?;
        attach_stream(
            runtime,
            source_id,
            "hyperliquid",
            market_type,
            "crypto",
            [
                crate::ObservationKind::Trade,
                crate::ObservationKind::OrderBook,
            ],
            provider,
        )
    }

    /// Attach the async-first Massive live source to the Actor input channel.
    pub fn attach_massive_market_source(
        runtime: &mut MarketApplication,
        product: MarketProduct,
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
    ) -> Result<(), String> {
        let (source_id, market_type) = match product {
            MarketProduct::Equity => ("massive.public.websocket.equity", "equity"),
            MarketProduct::Options => ("massive.public.websocket.options", "options"),
            _ => return Err("Massive market source requires equity or options product".into()),
        };
        attach_massive_source_with_id(
            runtime,
            source_id,
            market_type,
            "equity",
            product,
            api_key,
            endpoint,
        )
    }

    pub(crate) fn attach_massive_source_with_id(
        runtime: &mut MarketApplication,
        source_id: &str,
        route_market_type: &str,
        asset_type: &str,
        product: MarketProduct,
        api_key: impl Into<String>,
        endpoint: impl Into<String>,
    ) -> Result<(), String> {
        let config = MassiveWebSocketConfig {
            binding_id: source_id.into(),
            environment: "public".into(),
            endpoint: endpoint.into(),
            api_key: secrecy::SecretString::new(api_key.into().into()),
            event_capacity: 4_096,
        };
        let mut descriptor = SourceDescriptor::all_routes(SourceId::new(source_id)?);
        descriptor.market_type = Some(
            kairos_primitives::ProviderProductCode::new(route_market_type)
                .map_err(|error| error.to_string())?,
        );
        descriptor.asset_type = Some(
            asset_type
                .parse::<kairos_primitives::AssetClass>()
                .map_err(|error| error.to_string())?,
        );
        let descriptor = descriptor.with_observation_capabilities([
            crate::ObservationKind::Quote,
            crate::ObservationKind::Trade,
        ]);
        match product {
            MarketProduct::Equity => runtime.attach_source(spawn_stream(
                descriptor,
                MassiveStocksWebSocketConnection::new(config).map_err(|error| error.to_string())?,
                runtime.source_input_capacity(),
            )),
            MarketProduct::Options => runtime.attach_source(spawn_stream(
                descriptor,
                MassiveOptionsWebSocketConnection::new(config)
                    .map_err(|error| error.to_string())?,
                runtime.source_input_capacity(),
            )),
            _ => Err("Massive market source requires equity or options product".into()),
        }
    }
}

pub(crate) use manual::{attach_binance_snapshot, attach_stream};
pub use manual::{
    attach_hyperliquid_live_source, attach_hyperliquid_snapshot_source,
    attach_massive_market_source, attach_okx_live_source, attach_okx_snapshot_source,
    default_endpoint, MarketProduct,
};
