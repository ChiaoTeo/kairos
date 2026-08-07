//! Lifecycle abstraction for one physical provider connection.
//!
//! A connection hides its HTTP/WS client, SDK, retry policy and subscriptions.
//! Business actors see only the capability-specific trait they receive from a
//! gateway; they never own provider transport details.

use crate::application::connection::Connection;
use crate::domain::{ConnectionHealth, ConnectionLifecycle, ConnectionSpec, ConnectionState};

pub trait ConnectionComponent: Send {
    fn start(&mut self) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
    fn reconnect(&mut self) -> Result<(), String>;
}

pub struct ManagedConnection {
    spec: ConnectionSpec,
    state: ConnectionState,
    components: Vec<Box<dyn ConnectionComponent>>,
}

impl ManagedConnection {
    pub fn new(
        spec: ConnectionSpec,
        components: Vec<Box<dyn ConnectionComponent>>,
    ) -> Result<Self, String> {
        spec.validate()?;
        let identity = crate::domain::ConnectionIdentity::new(
            spec.connection_id.clone(),
            spec.provider.clone(),
            spec.product,
            spec.access,
            spec.transport,
            spec.capability,
        )?;
        Ok(Self {
            state: ConnectionState::new(identity),
            spec,
            components,
        })
    }

    fn fail(&mut self, error: String) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Failed;
        self.state.last_error = Some(error.clone());
        Err(error)
    }
}

impl Connection for ManagedConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        &self.state.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        if self.state.lifecycle == ConnectionLifecycle::Ready {
            return Ok(());
        }
        self.state.lifecycle = ConnectionLifecycle::Starting;
        let mut started = 0;
        for component in &mut self.components {
            if let Err(error) = component.start() {
                for component in self.components[..started].iter_mut().rev() {
                    let _ = component.stop();
                }
                return self.fail(error);
            }
            started += 1;
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.authenticated = self.spec.credential_id.is_some();
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        if matches!(
            self.state.lifecycle,
            ConnectionLifecycle::Created | ConnectionLifecycle::Stopped
        ) {
            self.state.lifecycle = ConnectionLifecycle::Stopped;
            return Ok(());
        }
        self.state.lifecycle = ConnectionLifecycle::Stopping;
        for component in self.components.iter_mut().rev() {
            if let Err(error) = component.stop() {
                return self.fail(error);
            }
        }
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.state.lifecycle = ConnectionLifecycle::Starting;
        for component in &mut self.components {
            if let Err(error) = component.reconnect() {
                return self.fail(error);
            }
        }
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.reconnect_count += 1;
        self.state.last_error = None;
        Ok(())
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: matches!(
                self.state.lifecycle,
                ConnectionLifecycle::Ready | ConnectionLifecycle::Degraded
            ),
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Connection, ConnectionComponent, ManagedConnection};
    use crate::application::ConnectionSpec;
    use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};

    struct Component {
        starts: usize,
        stops: usize,
    }

    impl ConnectionComponent for Component {
        fn start(&mut self) -> Result<(), String> {
            self.starts += 1;
            Ok(())
        }
        fn stop(&mut self) -> Result<(), String> {
            self.stops += 1;
            Ok(())
        }
        fn reconnect(&mut self) -> Result<(), String> {
            self.starts += 1;
            Ok(())
        }
    }

    #[test]
    fn managed_connection_owns_component_lifecycle() {
        let spec = ConnectionSpec {
            connection_id: "reference.binance.spot.rest".into(),
            provider: "binance".into(),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        };
        let mut connection = ManagedConnection::new(
            spec,
            vec![Box::new(Component {
                starts: 0,
                stops: 0,
            })],
        )
        .unwrap();
        assert_eq!(
            connection.state().lifecycle,
            crate::domain::ConnectionLifecycle::Created
        );
        connection.start().unwrap();
        assert!(connection.health().healthy);
        connection.reconnect().unwrap();
        assert_eq!(connection.state().reconnect_count, 1);
        connection.stop().unwrap();
        assert_eq!(
            connection.state().lifecycle,
            crate::domain::ConnectionLifecycle::Stopped
        );
    }
}
