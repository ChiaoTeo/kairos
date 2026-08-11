use crate::application::{
    AccountEventStreamConnection, AccountReadConnection, IntegrationError, OrderEntryConnection,
    OrderEventSource,
};
use crate::services::participants::ibkr::{
    IbkrAccountConnection, IbkrAccountStreamConnection, IbkrExecutionStreamConnection, IbkrOptions,
    IbkrOrderConnection,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IbkrConnectionConfig {
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

impl IbkrConnectionConfig {
    fn options(&self) -> Result<IbkrOptions, IntegrationError> {
        IbkrOptions::new(self.host.clone(), self.port, self.client_id)
            .map_err(IntegrationError::InvalidRequest)
    }
}

pub fn blocking_order_entry(
    config: &IbkrConnectionConfig,
) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
    IbkrOrderConnection::new(config.options()?)
        .map(|value| Box::new(value) as Box<dyn OrderEntryConnection>)
        .map_err(IntegrationError::InvalidRequest)
}

pub fn blocking_account(
    config: &IbkrConnectionConfig,
) -> Result<Box<dyn AccountReadConnection + Send>, IntegrationError> {
    IbkrAccountConnection::new(config.options()?)
        .map(|value| Box::new(value) as Box<dyn AccountReadConnection + Send>)
        .map_err(IntegrationError::InvalidRequest)
}

pub fn blocking_account_stream(
    config: &IbkrConnectionConfig,
    account_id: impl Into<String>,
    segment_key: impl Into<String>,
) -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> {
    IbkrAccountStreamConnection::new(config.options()?, account_id, segment_key)
        .map(|value| Box::new(value) as Box<dyn AccountEventStreamConnection>)
        .map_err(IntegrationError::InvalidRequest)
}

pub fn blocking_execution_stream(
    config: &IbkrConnectionConfig,
    account_id: impl Into<String>,
    symbol: Option<String>,
) -> Result<Box<dyn OrderEventSource>, IntegrationError> {
    IbkrExecutionStreamConnection::new(config.options()?, account_id, symbol)
        .map(|value| Box::new(value) as Box<dyn OrderEventSource>)
        .map_err(IntegrationError::InvalidRequest)
}
