use crate::application::{
    AccountEventStreamConnection, AccountReadConnection, AsyncAccountEventSource,
    AsyncAccountReadConnection, AsyncOrderEntryConnection, AsyncOrderEventSource,
    AsyncOrderQueryConnection, CommandResult, ConnectionDescriptor, ExternalEventEnvelope,
    ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery, IntegrationError, OrderEntryEvent,
    OrderEntryRequest,
};
use crate::domain::{ConnectionHealth, ParticipantKind, ParticipantRef};
use crate::services::participants::ibkr::{
    async_account::{IbkrAsyncAccountEvents, IbkrAsyncAccountRead},
    async_execution::{
        IbkrAsyncOrderEntry, IbkrAsyncOrderEvents, IbkrAsyncOrderQuery, IbkrAsyncSession,
    },
    IbkrAccountConnection, IbkrAccountStreamConnection, IbkrOptions,
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

/// One IBKR TWS/Gateway principal context. All projected execution
/// capabilities share one native async client and one serialized order-id
/// allocator for this client id.
pub struct IbkrConnection {
    descriptor: ConnectionDescriptor,
    account_id: String,
    session: std::sync::Arc<IbkrAsyncSession>,
}

impl IbkrConnection {
    pub fn connect(
        config: IbkrConnectionConfig,
        binding_id: impl Into<String>,
        account_id: impl Into<String>,
    ) -> Result<Self, IntegrationError> {
        let account_id = account_id.into();
        let participant = ParticipantRef::new(ParticipantKind::Broker, "ibkr")
            .map_err(IntegrationError::InvalidRequest)?;
        let mut descriptor = ConnectionDescriptor::new(binding_id, participant, "trading")
            .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = "live".into();
        descriptor.principal_id = Some(format!("client-id:{}", config.client_id));
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            descriptor,
            session: IbkrAsyncSession::new(config.options()?, account_id.clone()),
            account_id,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub fn order_entry(&self) -> IbkrOrderEntry {
        IbkrOrderEntry {
            inner: IbkrAsyncOrderEntry {
                session: self.session.clone(),
            },
        }
    }

    pub fn order_query(&self) -> IbkrOrderQuery {
        IbkrOrderQuery {
            inner: IbkrAsyncOrderQuery {
                session: self.session.clone(),
                descriptor: self.descriptor.clone(),
                account_id: self.account_id.clone(),
            },
        }
    }

    pub fn order_events(&self, symbol: Option<String>) -> IbkrOrderEvents {
        IbkrOrderEvents {
            inner: IbkrAsyncOrderEvents::new(
                self.session.clone(),
                self.descriptor.clone(),
                self.account_id.clone(),
                symbol,
            ),
        }
    }

    pub fn account_read(&self) -> IbkrAccountRead {
        IbkrAccountRead {
            inner: IbkrAsyncAccountRead {
                session: self.session.clone(),
            },
        }
    }

    pub fn account_events(
        &self,
        segment_key: impl Into<String>,
    ) -> Result<IbkrAccountEvents, IntegrationError> {
        IbkrAsyncAccountEvents::new(
            self.session.clone(),
            format!("{}.account-events", self.descriptor.binding_id),
            self.account_id.clone(),
            segment_key,
        )
        .map(|inner| IbkrAccountEvents { inner })
    }
}

pub struct IbkrAccountRead {
    inner: IbkrAsyncAccountRead,
}

pub struct IbkrAccountEvents {
    inner: IbkrAsyncAccountEvents,
}

pub struct IbkrOrderEntry {
    inner: IbkrAsyncOrderEntry,
}

pub struct IbkrOrderQuery {
    inner: IbkrAsyncOrderQuery,
}

pub struct IbkrOrderEvents {
    inner: IbkrAsyncOrderEvents,
}

impl AsyncOrderEntryConnection for IbkrOrderEntry {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner.submit(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.inner
            .cancel(request, remote_order_id, at_unix_nanos)
            .await
    }
}

impl AsyncOrderQueryConnection for IbkrOrderQuery {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.open_orders(query).await
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.inner.history(query).await
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let mut rows = self.inner.open_orders(query).await?;
        if rows.is_empty() {
            rows = self.inner.history(query).await?;
        }
        Ok(rows.into_iter().next())
    }
}

impl AsyncOrderEventSource for IbkrOrderEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect();
        Ok(())
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.inner.health()
    }

    async fn next_order_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.inner.next_event().await
    }
}

impl AsyncAccountReadConnection for IbkrAccountRead {
    async fn fetch_account(
        &mut self,
        segment: &crate::application::ExternalAccountSegment,
    ) -> Result<crate::application::ExternalAccountSnapshot, IntegrationError> {
        self.inner.fetch_account(segment).await
    }
}

impl AsyncAccountEventSource for IbkrAccountEvents {
    async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.connect_channel().await
    }

    async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.disconnect_channel().await
    }

    async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        self.inner.reconnect_channel().await
    }

    fn channel_health(&self) -> ConnectionHealth {
        self.inner.channel_health()
    }

    async fn next_account_event(
        &mut self,
    ) -> Result<crate::application::ExternalAccountEventEnvelope, IntegrationError> {
        self.inner.next_account_event().await
    }
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
