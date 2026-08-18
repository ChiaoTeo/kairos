//! Synchronous lifecycle and health capabilities.

use crate::{ConnectionHealth, IntegrationError};

pub trait ConnectionHealthQuery: Send {
    fn connection_health(&mut self) -> ConnectionHealth;
}

pub trait ConnectionLifecycleCommand: Send {
    fn connect(&mut self) -> Result<(), IntegrationError>;
    fn disconnect(&mut self) -> Result<(), IntegrationError>;
    fn reconnect(&mut self) -> Result<(), IntegrationError>;
}
