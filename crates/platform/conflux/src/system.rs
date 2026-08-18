use kairos_account_contract::view::AccountViewReader;
use kairos_account_contract::{AccountClient, AccountEventStream, AccountViewPublisher};
use kairos_execution_contract::{
    ExecutionClient, ExecutionEventStream, ExecutionViewPublisher, ExecutionViewReader,
};
use kairos_integration::participants::{
    binance::{
        advanced::{
            algo::BinanceAlgoTradingRestConnection,
            alpha::{BinanceAlphaTradingRestConnection, BinanceAlphaTradingWebSocketConnection},
            copy::BinanceCopyTradingRestConnection,
            loan::BinanceInstitutionalLoanRestConnection,
            portfolio::{
                pro::BinancePortfolioMarginProRestConnection, BinancePortfolioMarginRestConnection,
                BinancePortfolioMarginUserWebSocketConnection,
            },
            stocks::{
                BinanceStocksRestConnection, BinanceStocksUserWebSocketConnection,
                BinanceStocksWebSocketConnection,
            },
        },
        coinm::{
            BinanceCoinMRestConnection, BinanceCoinMUserWebSocketConnection,
            BinanceCoinMWebSocketApiConnection, BinanceCoinMWebSocketConnection,
        },
        funding::BinanceFundingRestConnection,
        margin::{
            BinanceMarginRestConnection, BinanceMarginUserWebSocketConnection,
            BinanceMarginWebSocketConnection,
        },
        options::{
            BinanceOptionsRestConnection, BinanceOptionsUserWebSocketConnection,
            BinanceOptionsWebSocketConnection,
        },
        spot::{
            BinanceSpotRestConnection, BinanceSpotUserWebSocketConnection,
            BinanceSpotWebSocketApiConnection, BinanceSpotWebSocketConnection,
        },
        usdm::{
            BinanceUsdMRestConnection, BinanceUsdMUserWebSocketConnection,
            BinanceUsdMWebSocketApiConnection, BinanceUsdMWebSocketConnection,
        },
    },
    hyperliquid::{
        account::HyperliquidAccountRestConnection, exchange::HyperliquidExchangeRestConnection,
        info::HyperliquidInfoRestConnection, HyperliquidWebSocketConnection,
    },
    ibkr::{
        IbkrAccountQueryConnection, IbkrAccountStreamConnection, IbkrExecutionStreamConnection,
        IbkrMarketDataConnection, IbkrOrderConnection,
    },
    massive::{
        MassiveOptionsWebSocketConnection, MassiveRestConnection, MassiveStocksWebSocketConnection,
    },
    okx::{
        private::{OkxPrivateRestConnection, OkxPrivateWebSocketConnection},
        public::{OkxPublicRestConnection, OkxPublicWebSocketConnection},
    },
};
use kairos_market_contract::{
    MarketClient, MarketEventStream, MarketViewPublisher, MarketViewReader,
};
use kairos_reference_contract::{ReferenceClient, ReferenceEventStream};
use kairos_risk_contract::{
    MmapRiskSnapshotPublisher, RiskAeronEventPublisher, RiskClient, RiskEventStream, RiskViewReader,
};
use kairos_transport::{
    AeronBytePublisher, AeronByteSubscription, SharedSnapshotReader, SharedSnapshotWriter,
};

use crate::{ManagedClients, ManagedConnections, NamedResources};

/// The one concrete resource universe available to every Kairos Actor.
pub struct ConfluxSystem {
    pub account_clients: ManagedClients<String, AccountClient>,
    pub execution_clients: ManagedClients<String, ExecutionClient>,
    pub market_clients: ManagedClients<String, MarketClient>,
    pub reference_clients: ManagedClients<String, ReferenceClient>,
    pub risk_clients: ManagedClients<String, RiskClient>,

    pub account_event_streams: NamedResources<String, AccountEventStream>,
    pub execution_event_streams: NamedResources<String, ExecutionEventStream>,
    pub market_event_streams: NamedResources<String, MarketEventStream>,
    pub reference_event_streams: NamedResources<String, ReferenceEventStream>,
    pub risk_event_streams: NamedResources<String, RiskEventStream>,

    pub account_view_readers: NamedResources<String, AccountViewReader>,
    pub account_view_publishers: NamedResources<String, AccountViewPublisher>,
    pub execution_view_readers: NamedResources<String, ExecutionViewReader>,
    pub execution_view_publishers: NamedResources<String, ExecutionViewPublisher>,
    pub market_view_readers: NamedResources<String, MarketViewReader>,
    pub market_view_publishers: NamedResources<String, MarketViewPublisher>,
    pub risk_view_readers: NamedResources<String, RiskViewReader>,
    pub risk_snapshot_publishers: NamedResources<String, MmapRiskSnapshotPublisher>,
    pub risk_event_publishers: NamedResources<String, RiskAeronEventPublisher>,

    /// Process-owned transport resources. These collections manage concrete
    /// Aeron and mmap handles without pretending that their byte APIs are a
    /// business Contract. Contract codecs remain owned by each module.
    pub aeron_publishers: NamedResources<String, AeronBytePublisher>,
    pub aeron_subscriptions: NamedResources<String, AeronByteSubscription>,
    pub mmap_readers: NamedResources<String, SharedSnapshotReader>,
    pub mmap_writers: NamedResources<String, SharedSnapshotWriter>,

    pub binance_spot_rest_connections: ManagedConnections<String, BinanceSpotRestConnection>,
    pub binance_spot_websocket_connections:
        ManagedConnections<String, BinanceSpotWebSocketConnection>,
    pub binance_spot_user_websocket_connections:
        ManagedConnections<String, BinanceSpotUserWebSocketConnection>,
    pub binance_spot_websocket_api_connections:
        ManagedConnections<String, BinanceSpotWebSocketApiConnection>,
    pub binance_funding_rest_connections: ManagedConnections<String, BinanceFundingRestConnection>,
    pub binance_margin_rest_connections: ManagedConnections<String, BinanceMarginRestConnection>,
    pub binance_margin_websocket_connections:
        ManagedConnections<String, BinanceMarginWebSocketConnection>,
    pub binance_margin_user_websocket_connections:
        ManagedConnections<String, BinanceMarginUserWebSocketConnection>,
    pub binance_usdm_rest_connections: ManagedConnections<String, BinanceUsdMRestConnection>,
    pub binance_usdm_websocket_connections:
        ManagedConnections<String, BinanceUsdMWebSocketConnection>,
    pub binance_usdm_user_websocket_connections:
        ManagedConnections<String, BinanceUsdMUserWebSocketConnection>,
    pub binance_usdm_websocket_api_connections:
        ManagedConnections<String, BinanceUsdMWebSocketApiConnection>,
    pub binance_coinm_rest_connections: ManagedConnections<String, BinanceCoinMRestConnection>,
    pub binance_coinm_websocket_connections:
        ManagedConnections<String, BinanceCoinMWebSocketConnection>,
    pub binance_coinm_user_websocket_connections:
        ManagedConnections<String, BinanceCoinMUserWebSocketConnection>,
    pub binance_coinm_websocket_api_connections:
        ManagedConnections<String, BinanceCoinMWebSocketApiConnection>,
    pub binance_options_rest_connections: ManagedConnections<String, BinanceOptionsRestConnection>,
    pub binance_options_websocket_connections:
        ManagedConnections<String, BinanceOptionsWebSocketConnection>,
    pub binance_options_user_websocket_connections:
        ManagedConnections<String, BinanceOptionsUserWebSocketConnection>,
    pub binance_portfolio_rest_connections:
        ManagedConnections<String, BinancePortfolioMarginRestConnection>,
    pub binance_portfolio_user_websocket_connections:
        ManagedConnections<String, BinancePortfolioMarginUserWebSocketConnection>,
    pub binance_portfolio_pro_rest_connections:
        ManagedConnections<String, BinancePortfolioMarginProRestConnection>,
    pub binance_algo_rest_connections: ManagedConnections<String, BinanceAlgoTradingRestConnection>,
    pub binance_copy_rest_connections: ManagedConnections<String, BinanceCopyTradingRestConnection>,
    pub binance_loan_rest_connections:
        ManagedConnections<String, BinanceInstitutionalLoanRestConnection>,
    pub binance_alpha_rest_connections:
        ManagedConnections<String, BinanceAlphaTradingRestConnection>,
    pub binance_alpha_websocket_connections:
        ManagedConnections<String, BinanceAlphaTradingWebSocketConnection>,
    pub binance_stocks_rest_connections: ManagedConnections<String, BinanceStocksRestConnection>,
    pub binance_stocks_websocket_connections:
        ManagedConnections<String, BinanceStocksWebSocketConnection>,
    pub binance_stocks_user_websocket_connections:
        ManagedConnections<String, BinanceStocksUserWebSocketConnection>,

    pub okx_public_rest_connections: ManagedConnections<String, OkxPublicRestConnection>,
    pub okx_public_websocket_connections: ManagedConnections<String, OkxPublicWebSocketConnection>,
    pub okx_private_rest_connections: ManagedConnections<String, OkxPrivateRestConnection>,
    pub okx_private_websocket_connections:
        ManagedConnections<String, OkxPrivateWebSocketConnection>,

    pub hyperliquid_info_rest_connections:
        ManagedConnections<String, HyperliquidInfoRestConnection>,
    pub hyperliquid_account_rest_connections:
        ManagedConnections<String, HyperliquidAccountRestConnection>,
    pub hyperliquid_exchange_rest_connections:
        ManagedConnections<String, HyperliquidExchangeRestConnection>,
    pub hyperliquid_websocket_connections:
        ManagedConnections<String, HyperliquidWebSocketConnection>,

    pub ibkr_account_query_connections: ManagedConnections<String, IbkrAccountQueryConnection>,
    pub ibkr_account_stream_connections: ManagedConnections<String, IbkrAccountStreamConnection>,
    pub ibkr_order_connections: ManagedConnections<String, IbkrOrderConnection>,
    pub ibkr_execution_stream_connections:
        ManagedConnections<String, IbkrExecutionStreamConnection>,
    pub ibkr_market_data_connections: ManagedConnections<String, IbkrMarketDataConnection>,
    pub massive_rest_connections: ManagedConnections<String, MassiveRestConnection>,
    pub massive_stocks_websocket_connections:
        ManagedConnections<String, MassiveStocksWebSocketConnection>,
    pub massive_options_websocket_connections:
        ManagedConnections<String, MassiveOptionsWebSocketConnection>,
}

impl ConfluxSystem {
    pub fn new() -> Self {
        Self {
            account_clients: ManagedClients::new(),
            execution_clients: ManagedClients::new(),
            market_clients: ManagedClients::new(),
            reference_clients: ManagedClients::new(),
            risk_clients: ManagedClients::new(),
            account_event_streams: NamedResources::new(),
            execution_event_streams: NamedResources::new(),
            market_event_streams: NamedResources::new(),
            reference_event_streams: NamedResources::new(),
            risk_event_streams: NamedResources::new(),
            account_view_readers: NamedResources::new(),
            account_view_publishers: NamedResources::new(),
            execution_view_readers: NamedResources::new(),
            execution_view_publishers: NamedResources::new(),
            market_view_readers: NamedResources::new(),
            market_view_publishers: NamedResources::new(),
            risk_view_readers: NamedResources::new(),
            risk_snapshot_publishers: NamedResources::new(),
            risk_event_publishers: NamedResources::new(),
            aeron_publishers: NamedResources::new(),
            aeron_subscriptions: NamedResources::new(),
            mmap_readers: NamedResources::new(),
            mmap_writers: NamedResources::new(),
            binance_spot_rest_connections: ManagedConnections::new(),
            binance_spot_websocket_connections: ManagedConnections::new(),
            binance_spot_user_websocket_connections: ManagedConnections::new(),
            binance_spot_websocket_api_connections: ManagedConnections::new(),
            binance_funding_rest_connections: ManagedConnections::new(),
            binance_margin_rest_connections: ManagedConnections::new(),
            binance_margin_websocket_connections: ManagedConnections::new(),
            binance_margin_user_websocket_connections: ManagedConnections::new(),
            binance_usdm_rest_connections: ManagedConnections::new(),
            binance_usdm_websocket_connections: ManagedConnections::new(),
            binance_usdm_user_websocket_connections: ManagedConnections::new(),
            binance_usdm_websocket_api_connections: ManagedConnections::new(),
            binance_coinm_rest_connections: ManagedConnections::new(),
            binance_coinm_websocket_connections: ManagedConnections::new(),
            binance_coinm_user_websocket_connections: ManagedConnections::new(),
            binance_coinm_websocket_api_connections: ManagedConnections::new(),
            binance_options_rest_connections: ManagedConnections::new(),
            binance_options_websocket_connections: ManagedConnections::new(),
            binance_options_user_websocket_connections: ManagedConnections::new(),
            binance_portfolio_rest_connections: ManagedConnections::new(),
            binance_portfolio_user_websocket_connections: ManagedConnections::new(),
            binance_portfolio_pro_rest_connections: ManagedConnections::new(),
            binance_algo_rest_connections: ManagedConnections::new(),
            binance_copy_rest_connections: ManagedConnections::new(),
            binance_loan_rest_connections: ManagedConnections::new(),
            binance_alpha_rest_connections: ManagedConnections::new(),
            binance_alpha_websocket_connections: ManagedConnections::new(),
            binance_stocks_rest_connections: ManagedConnections::new(),
            binance_stocks_websocket_connections: ManagedConnections::new(),
            binance_stocks_user_websocket_connections: ManagedConnections::new(),
            okx_public_rest_connections: ManagedConnections::new(),
            okx_public_websocket_connections: ManagedConnections::new(),
            okx_private_rest_connections: ManagedConnections::new(),
            okx_private_websocket_connections: ManagedConnections::new(),
            hyperliquid_info_rest_connections: ManagedConnections::new(),
            hyperliquid_account_rest_connections: ManagedConnections::new(),
            hyperliquid_exchange_rest_connections: ManagedConnections::new(),
            hyperliquid_websocket_connections: ManagedConnections::new(),
            ibkr_account_query_connections: ManagedConnections::new(),
            ibkr_account_stream_connections: ManagedConnections::new(),
            ibkr_order_connections: ManagedConnections::new(),
            ibkr_execution_stream_connections: ManagedConnections::new(),
            ibkr_market_data_connections: ManagedConnections::new(),
            massive_rest_connections: ManagedConnections::new(),
            massive_stocks_websocket_connections: ManagedConnections::new(),
            massive_options_websocket_connections: ManagedConnections::new(),
        }
    }
}

impl Default for ConfluxSystem {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use kairos_transport::SnapshotEnvelopeMetadata;

    use super::*;

    #[test]
    fn mmap_reader_and_writer_are_named_managed_resources() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("risk.latest.mmap");
        let mut system = ConfluxSystem::new();
        let key = "risk-latest".to_owned();
        let writer = SharedSnapshotWriter::create(&path, 1_024).unwrap();
        system
            .mmap_writers
            .ensure_with(key.clone(), 1, || writer)
            .unwrap();
        let writer = system.mmap_writers.get_mut(&key).unwrap();
        writer
            .resource_mut()
            .publish_with_metadata(
                SnapshotEnvelopeMetadata {
                    resource_epoch: 1,
                    producer_incarnation: 1,
                    generation: 1,
                    applied_event_sequence: 7,
                    published_at_unix_nanos: 10,
                },
                b"risk-view",
            )
            .unwrap();
        writer.set_state(crate::ResourceState::Ready);

        let reader = SharedSnapshotReader::open(&path).unwrap();
        system
            .mmap_readers
            .ensure_with(key.clone(), 1, || reader)
            .unwrap();
        let frame = system
            .mmap_readers
            .get(&key)
            .unwrap()
            .resource()
            .read_payload()
            .unwrap();
        assert_eq!(frame.payload, b"risk-view");
        assert_eq!(frame.applied_event_sequence, 7);
        assert_eq!(
            system.mmap_writers.get(&key).unwrap().state(),
            crate::ResourceState::Ready
        );
    }
}
