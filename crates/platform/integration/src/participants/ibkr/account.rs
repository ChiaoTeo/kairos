use crate::participants::ibkr::{IbkrAccountQueryConfig, IbkrAccountStreamConfig, descriptor};
use crate::services::participants::ibkr::IbkrOptions;
use crate::services::participants::ibkr::account::{AccountQueryService, AccountStreamService};
use crate::services::participants::ibkr::execution::SessionService;
use crate::{
    AccountQuery, AccountStream, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionMaintenance, ConnectionState,
    ExternalAccountEventEnvelope, ExternalAccountSegment, ExternalAccountSnapshot,
    IntegrationError, MaintenanceOutcome,
};

pub struct IbkrAccountQueryConnection {
    state: ConnectionState,
    session: std::sync::Arc<SessionService>,
    query: AccountQueryService,
}

impl IbkrAccountQueryConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: IbkrAccountQueryConfig,
    ) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            connection_key,
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
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: IbkrAccountStreamConfig,
    ) -> Result<Self, IntegrationError> {
        require_account(&config.account_id)?;
        if config.segment_key.trim().is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "IBKR segment key is required".into(),
            ));
        }
        let options = IbkrOptions::new(config.host, config.port, config.client_id)
            .map_err(IntegrationError::InvalidRequest)?;
        let descriptor = descriptor(
            connection_key,
            config.environment,
            config.client_id,
            "account.stream",
        )?;
        let session = SessionService::new(options, config.account_id.clone());
        let stream = AccountStreamService::new(
            session.clone(),
            descriptor.connection_key.clone(),
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

passive_maintenance!(IbkrAccountQueryConnection);
passive_maintenance!(IbkrAccountStreamConnection);

impl AccountQuery for IbkrAccountQueryConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError> {
        self.query.fetch_account(segment).await
    }
}
impl AccountStream for IbkrAccountStreamConnection {
    fn poll_next(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<ExternalAccountEventEnvelope, IntegrationError>> {
        self.stream.poll_next(cx)
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
