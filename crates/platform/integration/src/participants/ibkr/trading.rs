use crate::participants::ibkr::{descriptor, IbkrExecutionStreamConfig, IbkrOrderConfig};
use crate::services::participants::ibkr::{
    execution::{ExecutionStreamService, OrderCommandService, OrderQueryService, SessionService},
    IbkrOptions,
};
use crate::{
    CommandResult, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionState, ExecutionStream,
    ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
    IntegrationError, OrderCommand, OrderEntryEvent, OrderEntryRequest, OrderQuery,
};

pub struct IbkrOrderConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    command: OrderCommandService,
    query: OrderQueryService,
}
impl IbkrOrderConnection {
    pub fn new(config: IbkrOrderConfig) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            config.binding_id,
            config.environment,
            config.client_id,
            "order",
        )?;
        let session = SessionService::new(options, config.account_id.clone());
        Ok(Self {
            state: ConnectionState::new(descriptor.clone()),
            command: OrderCommandService {
                session: session.clone(),
            },
            query: OrderQueryService {
                session: session.clone(),
                descriptor,
                account_id: config.account_id,
            },
            session,
        })
    }
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }
}

pub struct IbkrExecutionStreamConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    stream: ExecutionStreamService,
}
impl IbkrExecutionStreamConnection {
    pub fn new(config: IbkrExecutionStreamConfig) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            config.binding_id,
            config.environment,
            config.client_id,
            "execution.stream",
        )?;
        let session = SessionService::new(options, config.account_id.clone());
        let stream = ExecutionStreamService::new(
            session.clone(),
            descriptor.clone(),
            config.account_id,
            config.symbol,
        );
        Ok(Self {
            state: ConnectionState::new(descriptor),
            session,
            stream,
        })
    }
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }
}

impl ConnectionHealthQuery for IbkrOrderConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.state.health()
    }
}
impl ConnectionLifecycleCommand for IbkrOrderConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.session
            .connect()
            .await
            .inspect(|()| self.state.mark_ready(true))
            .inspect_err(|error| self.state.mark_failed(error.to_string()))
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        self.session.disconnect().await;
        self.state.mark_stopped();
        Ok(())
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await?;
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        Ok(())
    }
}
impl ConnectionHealthQuery for IbkrExecutionStreamConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.state.health()
    }
}
impl ConnectionLifecycleCommand for IbkrExecutionStreamConnection {
    async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        self.session
            .connect()
            .await
            .inspect(|()| self.state.mark_ready(true))
            .inspect_err(|error| self.state.mark_failed(error.to_string()))
    }
    async fn disconnect(&mut self) -> Result<(), IntegrationError> {
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        self.stream.disconnect();
        self.session.disconnect().await;
        self.state.mark_stopped();
        Ok(())
    }
    async fn reconnect(&mut self) -> Result<(), IntegrationError> {
        self.disconnect().await?;
        self.connect().await?;
        self.state.reconnect_count = self.state.reconnect_count.saturating_add(1);
        Ok(())
    }
}
impl OrderCommand for IbkrOrderConnection {
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        self.command.submit(request).await
    }
    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        self.command
            .cancel(request, remote_order_id, at_unix_nanos)
            .await
    }
}
impl OrderQuery for IbkrOrderConnection {
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.query.open_orders(query).await
    }
    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        self.query.history(query).await
    }
    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let mut rows = self.query.open_orders(query).await?;
        if rows.is_empty() {
            rows = self.query.history(query).await?;
        }
        Ok(rows.into_iter().next())
    }
}
impl ExecutionStream for IbkrExecutionStreamConnection {
    async fn next(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError> {
        self.stream.next_event().await
    }
}
fn require_account(value: &str) -> Result<(), IntegrationError> {
    if value.trim().is_empty() {
        Err(IntegrationError::InvalidRequest(
            "IBKR account id is required".into(),
        ))
    } else {
        Ok(())
    }
}
