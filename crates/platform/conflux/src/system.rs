/// The complete statically typed client and connection universe supplied by
/// system composition to every Conflux process.
///
/// Actors do not declare their own resource subsets. They receive the system
/// universe and create or use named instances on demand. Concrete system
/// implementations remain ordinary structs, so adding a resource type is a
/// compile-time change and never an erased catalog insertion.
pub trait ConfluxSystem: Send + 'static {
    type Clients: Send + 'static;
    type Connections: Send + 'static;

    fn clients(&self) -> &Self::Clients;
    fn clients_mut(&mut self) -> &mut Self::Clients;
    fn connections(&self) -> &Self::Connections;
    fn connections_mut(&mut self) -> &mut Self::Connections;
}

/// Basic system container. A workspace-level composition may instead provide
/// a named struct implementing [`ConfluxSystem`] directly.
pub struct StaticSystem<Clients, Connections> {
    clients: Clients,
    connections: Connections,
}

impl<Clients, Connections> StaticSystem<Clients, Connections> {
    pub fn new(clients: Clients, connections: Connections) -> Self {
        Self {
            clients,
            connections,
        }
    }

    pub fn into_parts(self) -> (Clients, Connections) {
        (self.clients, self.connections)
    }
}

impl<Clients, Connections> ConfluxSystem for StaticSystem<Clients, Connections>
where
    Clients: Send + 'static,
    Connections: Send + 'static,
{
    type Clients = Clients;
    type Connections = Connections;

    fn clients(&self) -> &Self::Clients {
        &self.clients
    }

    fn clients_mut(&mut self) -> &mut Self::Clients {
        &mut self.clients
    }

    fn connections(&self) -> &Self::Connections {
        &self.connections
    }

    fn connections_mut(&mut self) -> &mut Self::Connections {
        &mut self.connections
    }
}
