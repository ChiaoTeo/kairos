use std::path::PathBuf;

use crate::ExecutionControlClient;
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
