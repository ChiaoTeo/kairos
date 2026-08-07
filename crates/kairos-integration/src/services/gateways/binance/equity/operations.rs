//! Equity market operations.

use serde_json::Value;

use super::client::EquityClient;

pub struct BinanceEquityMarketOperations<C> {
    client: C,
}

impl<C: EquityClient> BinanceEquityMarketOperations<C> {
    pub fn new(client: C) -> Self {
        Self { client }
    }

    pub fn exchange_info(&self) -> Result<Value, String> {
        self.client
            .exchange_info()
            .map_err(|error| error.to_string())
    }
}
