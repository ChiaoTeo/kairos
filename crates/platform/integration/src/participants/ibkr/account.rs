use crate::participants::ibkr::{descriptor, IbkrAccountQueryConfig, IbkrAccountStreamConfig};
use crate::services::participants::ibkr::{
    account::{AccountQueryService, AccountStreamService},
    execution::SessionService,
    IbkrOptions,
};
use crate::{
    AccountQuery, AccountStream, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionState, ExternalAccountEventEnvelope,
    ExternalAccountSegment, ExternalAccountSnapshot, IntegrationError,
};

pub struct IbkrAccountQueryConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    query: AccountQueryService,
}

impl IbkrAccountQueryConnection {
    pub fn new(config: IbkrAccountQueryConfig) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            config.binding_id,
            config.environment,
            config.client_id,
            "account.query",
        )?;
        let session = SessionService::new(options, config.account_id);
        Ok(Self {
            state: ConnectionState::new(descriptor),
            query: AccountQueryService {
                session: session.clone(),
            },
            session,
        })
    }
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.state.identity
    }
}

pub struct IbkrAccountStreamConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    stream: AccountStreamService,
}

impl IbkrAccountStreamConnection {
    pub fn new(config: IbkrAccountStreamConfig) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        if config.segment_key.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "IBKR segment key is required".into(),
            ));
        }
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            config.binding_id,
            config.environment,
            config.client_id,
            "account.stream",
        )?;
        let session = SessionService::new(options, config.account_id.clone());
        let stream = AccountStreamService::new(
            session.clone(),
            descriptor.binding_id.clone(),
            config.account_id,
            config.segment_key,
        )?;
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

impl ConnectionHealthQuery for IbkrAccountQueryConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.state.health()
    }
}
impl ConnectionLifecycleCommand for IbkrAccountQueryConnection {
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
impl ConnectionHealthQuery for IbkrAccountStreamConnection {
    fn connection_health(&mut self) -> ConnectionHealth {
        self.state.health()
    }
}
impl ConnectionLifecycleCommand for IbkrAccountStreamConnection {
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
        self.stream.disconnect().await?;
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

impl AccountQuery for IbkrAccountQueryConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        self.query.fetch_account(segment).await
    }
}
impl AccountStream for IbkrAccountStreamConnection {
    async fn next(&mut self) -> Result<ExternalAccountEventEnvelope, IntegrationError> {
        self.stream.next().await
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
