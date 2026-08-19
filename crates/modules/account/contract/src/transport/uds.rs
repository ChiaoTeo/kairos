use std::path::PathBuf;

use crate::control::AccountControlClient;
pub struct AccountUdsTransport {
    pub control: AccountControlClient,
}
impl AccountUdsTransport {
    pub fn connect(socket: impl Into<PathBuf>) -> Self {
        Self {
            control: AccountControlClient::connect(socket),
        }
    }
}
