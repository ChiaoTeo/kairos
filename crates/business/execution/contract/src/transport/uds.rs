use crate::ExecutionControlClient;
use std::path::PathBuf;
pub struct ExecutionUdsTransport {
    pub control: ExecutionControlClient,
}
impl ExecutionUdsTransport {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            control: ExecutionControlClient::connect(socket),
        }
    }
}
