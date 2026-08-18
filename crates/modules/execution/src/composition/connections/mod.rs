use std::path::{Path, PathBuf};

pub use crate::services::gateway::ExecutionWriterFence;
use kairos_integration::blocking::OrderCommand as BlockingOrderCommand;
use kairos_integration::{
    CommandOutcome, ExecutionStream, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IntegrationError, OrderCommand, OrderQuery, ParticipantInstrumentRef,
    ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
};
use kairos_integration::{
    ConnectionDescriptor, ConnectionHealth, DecimalValue, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus,
};
use secrecy::SecretString;

mod entry;
mod events;
mod model;
mod query;
mod routes;

pub use entry::*;
pub use events::*;
pub use model::*;
pub use query::*;
pub(crate) use routes::install_execution_connections;
