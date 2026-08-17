use std::path::{Path, PathBuf};

use crate::application::ExecutionAsyncRoute;
use crate::services::gateway::{
    AsyncQueuedOrderEntry, AsyncQueuedOrderEventSource, AsyncQueuedOrderQuery,
};
use crate::services::routing::{RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection, CommandOutcome,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    IntegrationError, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ProviderInstrumentRef,
};
use kairos_integration::application::{
    ConnectionDescriptor, ConnectionHealth, DecimalValue, OrderEntryEvent, OrderEntryRequest,
    OrderEntryStatus,
};
use kairos_integration::blocking::{OrderEntryConnection, OrderEventSource, OrderQueryConnection};
use kairos_integration::participants::binance;
use kairos_integration::participants::binance::ConnectionDomain as BinanceConnectionDomain;
use secrecy::{ExposeSecret, SecretString};

use super::providers::{
    binance_spot_private_connection, compose_ibkr_async_execution, okx_private_connection,
    okx_trading_shape,
};

mod direct;
mod entry;
mod events;
mod model;
mod query;
mod routes;
mod writer_fence;

pub use direct::*;
pub use entry::*;
pub use events::*;
pub use model::*;
pub use query::*;
pub use routes::{compose_execution_connections, compose_execution_routes};
pub use writer_fence::*;

#[cfg(test)]
mod tests;
