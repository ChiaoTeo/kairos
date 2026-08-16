use std::path::PathBuf;

use crate::control::MarketControlClient;

pub struct MarketUdsTransport {
    pub control: MarketControlClient,
}

impl MarketUdsTransport {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            control: MarketControlClient::connect(socket),
        }
    }
}
