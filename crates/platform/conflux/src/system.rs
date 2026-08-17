use kairos_account_contract::AccountClient;
use kairos_execution_contract::ExecutionClient;
use kairos_integration::participants::{
    binance::{
        BinanceCoinMConnection, BinanceFuturesPrincipalConnection,
        BinanceMarginPrincipalConnection, BinanceOptionsConnection,
        BinanceOptionsPrincipalConnection, BinanceSpotConnection, BinanceSpotPrincipalConnection,
        BinanceUsdMConnection,
    },
    hyperliquid::HyperliquidConnection,
    ibkr::IbkrConnection,
    massive::MassiveConnection,
    okx::{OkxConnection, OkxPrincipalConnection},
};
use kairos_market_contract::MarketClient;
use kairos_reference_contract::ReferenceClient;
use kairos_risk_contract::RiskClient;

use crate::{ManagedClients, ManagedConnections};

/// The one concrete resource universe available to every Kairos Actor.
pub struct ConfluxSystem {
    pub account_clients: ManagedClients<String, AccountClient>,
    pub execution_clients: ManagedClients<String, ExecutionClient>,
    pub market_clients: ManagedClients<String, MarketClient>,
    pub reference_clients: ManagedClients<String, ReferenceClient>,
    pub risk_clients: ManagedClients<String, RiskClient>,

    pub binance_spot_connections: ManagedConnections<String, BinanceSpotConnection>,
    pub binance_usdm_connections: ManagedConnections<String, BinanceUsdMConnection>,
    pub binance_coinm_connections: ManagedConnections<String, BinanceCoinMConnection>,
    pub binance_options_connections: ManagedConnections<String, BinanceOptionsConnection>,
    pub binance_spot_principals: ManagedConnections<String, BinanceSpotPrincipalConnection>,
    pub binance_futures_principals: ManagedConnections<String, BinanceFuturesPrincipalConnection>,
    pub binance_margin_principals: ManagedConnections<String, BinanceMarginPrincipalConnection>,
    pub binance_options_principals: ManagedConnections<String, BinanceOptionsPrincipalConnection>,
    pub okx_connections: ManagedConnections<String, OkxConnection>,
    pub okx_principals: ManagedConnections<String, OkxPrincipalConnection>,
    pub hyperliquid_connections: ManagedConnections<String, HyperliquidConnection>,
    pub ibkr_connections: ManagedConnections<String, IbkrConnection>,
    pub massive_connections: ManagedConnections<String, MassiveConnection>,
}

impl ConfluxSystem {
    pub fn new() -> Self {
        Self {
            account_clients: ManagedClients::new(),
            execution_clients: ManagedClients::new(),
            market_clients: ManagedClients::new(),
            reference_clients: ManagedClients::new(),
            risk_clients: ManagedClients::new(),
            binance_spot_connections: ManagedConnections::new(),
            binance_usdm_connections: ManagedConnections::new(),
            binance_coinm_connections: ManagedConnections::new(),
            binance_options_connections: ManagedConnections::new(),
            binance_spot_principals: ManagedConnections::new(),
            binance_futures_principals: ManagedConnections::new(),
            binance_margin_principals: ManagedConnections::new(),
            binance_options_principals: ManagedConnections::new(),
            okx_connections: ManagedConnections::new(),
            okx_principals: ManagedConnections::new(),
            hyperliquid_connections: ManagedConnections::new(),
            ibkr_connections: ManagedConnections::new(),
            massive_connections: ManagedConnections::new(),
        }
    }
}

impl Default for ConfluxSystem {
    fn default() -> Self {
        Self::new()
    }
}
