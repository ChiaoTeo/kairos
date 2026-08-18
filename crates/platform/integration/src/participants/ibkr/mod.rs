//! Virtual provider-native connections backed by independent IBKR TWS client
//! sessions. Each connection has one capability ownership boundary and its own
//! client ID, even though the upstream library multiplexes a TCP protocol.

mod account;
mod config;
mod market;
mod trading;

pub use account::{IbkrAccountQueryConnection, IbkrAccountStreamConnection};
pub use config::{
    IbkrAccountQueryConfig, IbkrAccountStreamConfig, IbkrExecutionStreamConfig,
    IbkrMarketDataConfig, IbkrOrderConfig,
};
pub use market::IbkrMarketDataConnection;
pub use trading::{IbkrExecutionStreamConnection, IbkrOrderConnection};

fn descriptor(
    connection_key: crate::ConnectionKey,
    environment: String,
    client_id: i32,
    domain: &str,
) -> Result<crate::ConnectionDescriptor, crate::IntegrationError> {
    let mut descriptor = crate::ConnectionDescriptor::new(
        connection_key,
        crate::ParticipantRef::new(crate::ParticipantKind::Broker, "ibkr")
            .map_err(crate::IntegrationError::InvalidRequest)?,
        domain,
    )
    .map_err(crate::IntegrationError::InvalidRequest)?;
    descriptor.environment = environment;
    descriptor.principal_id = Some(format!("client-id:{client_id}"));
    descriptor
        .validate()
        .map_err(crate::IntegrationError::InvalidRequest)?;
    Ok(descriptor)
}
