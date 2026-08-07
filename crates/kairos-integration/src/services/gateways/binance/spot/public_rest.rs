//! Spot public REST operations.

use serde_json::Value;

use super::client::SpotClient;
use crate::services::drivers::http::ExchangeError;

pub trait SpotPublicRest {
    fn fetch_exchange_info(&self) -> Result<Value, ExchangeError>;
}

impl<T: SpotClient> SpotPublicRest for T {
    fn fetch_exchange_info(&self) -> Result<Value, ExchangeError> {
        self.exchange_info()
    }
}
