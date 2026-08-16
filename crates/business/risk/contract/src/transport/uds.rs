use crate::control::RiskControlClient;
use crate::ContractResult;
use std::path::PathBuf;
pub struct RiskUdsTransport {
    pub control: RiskControlClient,
}
impl RiskUdsTransport {
    pub fn connect(socket: impl Into<PathBuf>) -> ContractResult<Self> {
        Ok(Self {
            control: RiskControlClient::connect(socket.into())?,
        })
    }
}
