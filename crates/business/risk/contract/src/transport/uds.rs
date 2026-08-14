use std::path::PathBuf;
use crate::control::RiskControlClient;
use crate::ContractResult;
pub struct RiskUdsTransport { pub control: RiskControlClient }
impl RiskUdsTransport { pub fn connect(socket:impl Into<PathBuf>)->ContractResult<Self>{Ok(Self{control:RiskControlClient::connect(socket.into())?})} }
