use crate::participants::ibkr::{descriptor, IbkrExecutionStreamConfig, IbkrOrderConfig};
use crate::services::participants::ibkr::{
    execution::{ExecutionStreamService, OrderCommandService, OrderQueryService, SessionService},
    IbkrOptions,
};
use crate::{
    CommandResult, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionMaintenance, ConnectionState,
    ExecutionStream, ExternalEventEnvelope, ExternalExecutionEvent, ExternalOrder,
    ExternalOrderQuery, IntegrationError, MaintenanceOutcome, OrderCommand, OrderEntryEvent,
    OrderEntryRequest, OrderQuery,
};

pub struct IbkrOrderConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    command: OrderCommandService,
    query: OrderQueryService,
}
impl IbkrOrderConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: IbkrOrderConfig,
    ) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            connection_key,
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
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: IbkrExecutionStreamConfig,
    ) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            connection_key,
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
        let result = async {
            self.session.connect().await?;
            self.stream.connect().await
        }
        .await;
        result
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
macro_rules! passive_maintenance {
    ($connection:ty) => {
        impl ConnectionMaintenance for $connection {
            fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
                None
            }

            fn poll_maintenance(
                &mut self,
                _cx: &mut std::task::Context<'_>,
                _now: tokio::time::Instant,
            ) -> std::task::Poll<Result<MaintenanceOutcome, IntegrationError>> {
                std::task::Poll::Ready(Ok(MaintenanceOutcome::Healthy))
            }
        }
    };
}

passive_maintenance!(IbkrOrderConnection);
passive_maintenance!(IbkrExecutionStreamConnection);
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
    fn poll_next(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError>>
    {
        self.stream.poll_next_event(cx)
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
