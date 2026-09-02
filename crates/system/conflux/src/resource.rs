use std::collections::HashMap;
use std::future::Future;
use std::hash::Hash;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;

use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceState {
    Created,
    Starting,
    Ready,
    Degraded,
    Failed,
    Stopping,
    Retiring,
    Stopped,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RecoveryPolicy {
    pub initial_backoff: Duration,
    pub maximum_backoff: Duration,
    pub maximum_attempts: Option<u32>,
}

impl Default for RecoveryPolicy {
    fn default() -> Self {
        Self {
            initial_backoff: Duration::from_secs(1),
            maximum_backoff: Duration::from_secs(32),
            maximum_attempts: Some(6),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ConnectionCreateOptions {
    pub required: bool,
    pub recovery: RecoveryPolicy,
}

pub type ManagedConnectionPolicy = ConnectionCreateOptions;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnsureDisposition {
    Created,
    Existing,
    Replaced,
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum ResourceError {
    #[error("resource revision {received} is older than current revision {current}")]
    StaleRevision { current: u64, received: u64 },
    #[error("resource epoch is exhausted")]
    EpochExhausted,
    #[error("connection generation is exhausted")]
    GenerationExhausted,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ResourceOperationError<E> {
    #[error("named resource does not exist")]
    NotFound,
    #[error("resource operation failed: {0}")]
    Operation(E),
}

pub struct ManagedClient<C> {
    client: C,
    revision: u64,
    epoch: u64,
    state: ResourceState,
}

impl<C> ManagedClient<C> {
    fn new(client: C, revision: u64) -> Self {
        Self {
            client,
            revision,
            epoch: 0,
            state: ResourceState::Created,
        }
    }

    pub fn client(&self) -> &C {
        &self.client
    }

    pub fn client_mut(&mut self) -> &mut C {
        &mut self.client
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub const fn epoch(&self) -> u64 {
        self.epoch
    }

    pub const fn state(&self) -> ResourceState {
        self.state
    }

    pub fn set_state(&mut self, state: ResourceState) {
        self.state = state;
    }

    fn replace(&mut self, revision: u64, client: C) -> Result<(), ResourceError> {
        validate_new_revision(self.revision, revision)?;
        self.epoch = next_epoch(self.epoch)?;
        self.revision = revision;
        self.client = client;
        self.state = ResourceState::Created;
        Ok(())
    }
}

pub struct ManagedClients<K, C> {
    entries: HashMap<K, ManagedClient<C>>,
}

impl<K, C> Default for ManagedClients<K, C> {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
        }
    }
}

impl<K, C> ManagedClients<K, C>
where
    K: Eq + Hash,
{
    pub fn new() -> Self {
        Self::default()
    }

    pub fn ensure_with(
        &mut self,
        key: K,
        revision: u64,
        create: impl FnOnce() -> C,
    ) -> Result<EnsureDisposition, ResourceError> {
        self.ensure_with_entry(key, revision, create)
            .map(|(disposition, _)| disposition)
    }

    pub fn ensure_with_entry(
        &mut self,
        key: K,
        revision: u64,
        create: impl FnOnce() -> C,
    ) -> Result<(EnsureDisposition, &mut ManagedClient<C>), ResourceError> {
        match self.entries.entry(key) {
            std::collections::hash_map::Entry::Occupied(mut occupied) => {
                let entry = occupied.get_mut();
                let disposition = if revision < entry.revision {
                    return Err(ResourceError::StaleRevision {
                        current: entry.revision,
                        received: revision,
                    });
                } else if revision == entry.revision {
                    EnsureDisposition::Existing
                } else {
                    entry.replace(revision, create())?;
                    EnsureDisposition::Replaced
                };
                Ok((disposition, occupied.into_mut()))
            },
            std::collections::hash_map::Entry::Vacant(vacant) => Ok((
                EnsureDisposition::Created,
                vacant.insert(ManagedClient::new(create(), revision)),
            )),
        }
    }

    pub fn get(&self, key: &K) -> Option<&ManagedClient<C>> {
        self.entries.get(key)
    }

    pub fn get_mut(&mut self, key: &K) -> Option<&mut ManagedClient<C>> {
        self.entries.get_mut(key)
    }

    pub fn remove(&mut self, key: &K) -> Option<ManagedClient<C>> {
        self.entries.remove(key)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&K, &ManagedClient<C>)> {
        self.entries.iter()
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&K, &mut ManagedClient<C>)> {
        self.entries.iter_mut()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ManagedLifecycleOperation {
    Connect,
    Reconnect,
    Disconnect,
}

type ManagedLifecycleFuture<C> = Pin<
    Box<
        dyn Future<Output = (C, Result<(), kairos_integration::IntegrationError>)> + Send + 'static,
    >,
>;

struct ManagedLifecycle<C> {
    operation: ManagedLifecycleOperation,
    future: ManagedLifecycleFuture<C>,
}

pub struct ManagedConnection<C> {
    connection: Option<C>,
    lifecycle: Option<ManagedLifecycle<C>>,
    generation: u64,
    state: ResourceState,
    policy: ManagedConnectionPolicy,
}

impl<C> ManagedConnection<C> {
    fn new(connection: C, generation: u64, policy: ManagedConnectionPolicy) -> Self {
        Self {
            connection: Some(connection),
            lifecycle: None,
            generation,
            state: ResourceState::Created,
            policy,
        }
    }

    pub fn connection(&self) -> &C {
        self.connection
            .as_ref()
            .expect("managed connection is unavailable during lifecycle transition")
    }

    pub fn connection_mut(&mut self) -> &mut C {
        self.connection
            .as_mut()
            .expect("managed connection is unavailable during lifecycle transition")
    }

    pub const fn generation(&self) -> u64 {
        self.generation
    }

    pub const fn state(&self) -> ResourceState {
        self.state
    }

    pub const fn policy(&self) -> ManagedConnectionPolicy {
        self.policy
    }

    pub fn set_state(&mut self, state: ResourceState) {
        self.state = state;
    }
}

impl<C> ManagedConnection<C>
where
    C: kairos_integration::ConnectionLifecycleCommand + Send + 'static,
{
    pub(crate) fn begin_lifecycle(&mut self, operation: ManagedLifecycleOperation) {
        assert!(
            self.lifecycle.is_none(),
            "managed connection lifecycle is already in progress"
        );
        let mut connection = self
            .connection
            .take()
            .expect("managed connection is present before lifecycle transition");
        let future = Box::pin(async move {
            let result = match operation {
                ManagedLifecycleOperation::Connect => {
                    kairos_integration::ConnectionLifecycleCommand::connect(&mut connection).await
                },
                ManagedLifecycleOperation::Reconnect => {
                    kairos_integration::ConnectionLifecycleCommand::reconnect(&mut connection).await
                },
                ManagedLifecycleOperation::Disconnect => {
                    kairos_integration::ConnectionLifecycleCommand::disconnect(&mut connection)
                        .await
                },
            };
            (connection, result)
        });
        self.lifecycle = Some(ManagedLifecycle { operation, future });
    }

    pub(crate) fn poll_lifecycle(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<(
        ManagedLifecycleOperation,
        Result<(), kairos_integration::IntegrationError>,
    )> {
        let Some(lifecycle) = self.lifecycle.as_mut() else {
            return Poll::Pending;
        };
        let operation = lifecycle.operation;
        match lifecycle.future.as_mut().poll(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready((connection, result)) => {
                self.connection = Some(connection);
                self.lifecycle = None;
                Poll::Ready((operation, result))
            },
        }
    }

    pub(crate) fn lifecycle_in_progress(&self) -> bool {
        self.lifecycle.is_some()
    }
}

pub struct ManagedConnections<K, C> {
    entries: HashMap<K, ManagedConnection<C>>,
    generations: HashMap<K, u64>,
}

/// One named, concrete transport resource such as a typed Aeron stream or
/// contract-owned indexed reader/publisher. `R` is never erased.
pub struct ManagedResource<R> {
    resource: R,
    revision: u64,
    epoch: u64,
    state: ResourceState,
}

impl<R> ManagedResource<R> {
    fn new(resource: R, revision: u64) -> Self {
        Self {
            resource,
            revision,
            epoch: 0,
            state: ResourceState::Created,
        }
    }

    pub fn resource(&self) -> &R {
        &self.resource
    }

    pub fn resource_mut(&mut self) -> &mut R {
        &mut self.resource
    }

    pub fn into_resource(self) -> R {
        self.resource
    }

    pub const fn revision(&self) -> u64 {
        self.revision
    }

    pub const fn epoch(&self) -> u64 {
        self.epoch
    }

    pub const fn state(&self) -> ResourceState {
        self.state
    }

    pub fn set_state(&mut self, state: ResourceState) {
        self.state = state;
    }

    fn replace(&mut self, revision: u64, resource: R) -> Result<(), ResourceError> {
        validate_new_revision(self.revision, revision)?;
        self.epoch = next_epoch(self.epoch)?;
        self.revision = revision;
        self.resource = resource;
        self.state = ResourceState::Created;
        Ok(())
    }
}

pub struct NamedResources<K, R> {
    entries: HashMap<K, ManagedResource<R>>,
}

impl<K, R> Default for NamedResources<K, R> {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
        }
    }
}

impl<K, R> NamedResources<K, R>
where
    K: Eq + Hash,
{
    pub fn new() -> Self {
        Self::default()
    }

    pub fn ensure_with(
        &mut self,
        key: K,
        revision: u64,
        create: impl FnOnce() -> R,
    ) -> Result<EnsureDisposition, ResourceError> {
        self.ensure_with_entry(key, revision, create)
            .map(|(disposition, _)| disposition)
    }

    pub fn ensure_with_entry(
        &mut self,
        key: K,
        revision: u64,
        create: impl FnOnce() -> R,
    ) -> Result<(EnsureDisposition, &mut ManagedResource<R>), ResourceError> {
        match self.entries.entry(key) {
            std::collections::hash_map::Entry::Occupied(mut occupied) => {
                let entry = occupied.get_mut();
                let disposition = if revision < entry.revision {
                    return Err(ResourceError::StaleRevision {
                        current: entry.revision,
                        received: revision,
                    });
                } else if revision == entry.revision {
                    EnsureDisposition::Existing
                } else {
                    entry.replace(revision, create())?;
                    EnsureDisposition::Replaced
                };
                Ok((disposition, occupied.into_mut()))
            },
            std::collections::hash_map::Entry::Vacant(vacant) => Ok((
                EnsureDisposition::Created,
                vacant.insert(ManagedResource::new(create(), revision)),
            )),
        }
    }

    pub fn get(&self, key: &K) -> Option<&ManagedResource<R>> {
        self.entries.get(key)
    }

    pub fn get_mut(&mut self, key: &K) -> Option<&mut ManagedResource<R>> {
        self.entries.get_mut(key)
    }

    /// Runs one operation against a named resource and records operational
    /// readiness consistently for every concrete indexed/Aeron Contract type.
    pub fn try_with<T, E>(
        &mut self,
        key: &K,
        operation: impl FnOnce(&mut R) -> Result<T, E>,
    ) -> Result<T, ResourceOperationError<E>> {
        let resource = self
            .entries
            .get_mut(key)
            .ok_or(ResourceOperationError::NotFound)?;
        match operation(resource.resource_mut()) {
            Ok(value) => {
                resource.set_state(ResourceState::Ready);
                Ok(value)
            },
            Err(error) => {
                resource.set_state(ResourceState::Degraded);
                Err(ResourceOperationError::Operation(error))
            },
        }
    }

    pub fn remove(&mut self, key: &K) -> Option<ManagedResource<R>> {
        self.entries.remove(key)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&K, &ManagedResource<R>)> {
        self.entries.iter()
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&K, &mut ManagedResource<R>)> {
        self.entries.iter_mut()
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub(crate) fn clear(&mut self) {
        self.entries.clear();
    }
}

impl<K, C> Default for ManagedConnections<K, C> {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
            generations: HashMap::new(),
        }
    }
}

impl<K, C> ManagedConnections<K, C>
where
    K: Clone + Eq + Hash,
{
    pub fn new() -> Self {
        Self::default()
    }

    #[cfg(test)]
    pub(crate) fn insert_new(&mut self, key: K, connection: C) -> Result<bool, ResourceError> {
        self.insert_new_with_options(key, connection, ConnectionCreateOptions::default())
    }

    pub(crate) fn insert_new_with_options(
        &mut self,
        key: K,
        connection: C,
        options: ConnectionCreateOptions,
    ) -> Result<bool, ResourceError> {
        if self.entries.contains_key(&key) {
            return Ok(false);
        }
        let generation = next_generation(self.generations.get(&key).copied())?;
        self.generations.insert(key.clone(), generation);
        self.entries
            .insert(key, ManagedConnection::new(connection, generation, options));
        Ok(true)
    }

    pub fn get(&self, key: &K) -> Option<&ManagedConnection<C>> {
        self.entries.get(key)
    }

    pub fn get_mut(&mut self, key: &K) -> Option<&mut ManagedConnection<C>> {
        self.entries.get_mut(key)
    }

    pub fn remove(&mut self, key: &K) -> Option<ManagedConnection<C>> {
        self.entries.remove(key)
    }

    pub fn iter(&self) -> impl Iterator<Item = (&K, &ManagedConnection<C>)> {
        self.entries.iter()
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&K, &mut ManagedConnection<C>)> {
        self.entries.iter_mut()
    }
}

fn validate_new_revision(current: u64, received: u64) -> Result<(), ResourceError> {
    if received <= current {
        return Err(ResourceError::StaleRevision { current, received });
    }
    Ok(())
}

fn next_epoch(current: u64) -> Result<u64, ResourceError> {
    current.checked_add(1).ok_or(ResourceError::EpochExhausted)
}

fn next_generation(current: Option<u64>) -> Result<u64, ResourceError> {
    current
        .unwrap_or_default()
        .checked_add(1)
        .ok_or(ResourceError::GenerationExhausted)
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    struct SlowConnection {
        connects: Arc<AtomicUsize>,
        drops: Arc<AtomicUsize>,
    }

    impl Drop for SlowConnection {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    impl kairos_integration::ConnectionLifecycleCommand for SlowConnection {
        async fn connect(&mut self) -> Result<(), kairos_integration::IntegrationError> {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            self.connects.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }

        async fn disconnect(&mut self) -> Result<(), kairos_integration::IntegrationError> {
            Ok(())
        }

        async fn reconnect(&mut self) -> Result<(), kairos_integration::IntegrationError> {
            self.connect().await
        }
    }

    #[test]
    fn named_resources_replace_only_on_a_newer_revision() {
        let mut resources = NamedResources::new();
        let key = "risk-view".to_owned();

        assert_eq!(
            resources.ensure_with(key.clone(), 3, || 10).unwrap(),
            EnsureDisposition::Created
        );
        resources
            .get_mut(&key)
            .unwrap()
            .set_state(ResourceState::Ready);
        assert_eq!(
            resources.ensure_with(key.clone(), 3, || 20).unwrap(),
            EnsureDisposition::Existing
        );
        assert_eq!(*resources.get(&key).unwrap().resource(), 10);
        assert_eq!(resources.get(&key).unwrap().epoch(), 0);
        assert_eq!(resources.get(&key).unwrap().state(), ResourceState::Ready);

        assert_eq!(
            resources.ensure_with(key.clone(), 4, || 20).unwrap(),
            EnsureDisposition::Replaced
        );
        let resource = resources.get(&key).unwrap();
        assert_eq!(*resource.resource(), 20);
        assert_eq!(resource.revision(), 4);
        assert_eq!(resource.epoch(), 1);
        assert_eq!(resource.state(), ResourceState::Created);

        assert_eq!(
            resources.ensure_with(key, 2, || 30),
            Err(ResourceError::StaleRevision {
                current: 4,
                received: 2,
            })
        );
    }

    #[test]
    fn named_resource_operation_updates_readiness() {
        let mut resources = NamedResources::new();
        let key = "market-events".to_owned();
        resources.ensure_with(key.clone(), 1, || 10_u64).unwrap();

        let value = resources
            .try_with(&key, |resource| -> Result<u64, &'static str> {
                *resource += 1;
                Ok(*resource)
            })
            .unwrap();
        assert_eq!(value, 11);
        assert_eq!(resources.get(&key).unwrap().state(), ResourceState::Ready);

        assert_eq!(
            resources.try_with(&key, |_resource| Err::<(), _>("publish failed")),
            Err(ResourceOperationError::Operation("publish failed"))
        );
        assert_eq!(
            resources.get(&key).unwrap().state(),
            ResourceState::Degraded
        );
        assert_eq!(
            resources.try_with(&"missing".to_owned(), |_resource| Ok::<(), ()>(())),
            Err(ResourceOperationError::NotFound)
        );
    }

    #[test]
    fn connection_generation_survives_remove_and_recreate() {
        let mut connections = ManagedConnections::new();
        let key = "execution-main".to_owned();
        assert!(connections.insert_new(key.clone(), 10).unwrap());
        assert_eq!(connections.get(&key).unwrap().generation(), 1);

        drop(connections.remove(&key));
        assert!(connections.insert_new(key.clone(), 20).unwrap());
        assert_eq!(connections.get(&key).unwrap().generation(), 2);
    }

    #[tokio::test]
    async fn lifecycle_future_remains_owned_by_the_managed_connection() {
        let connects = Arc::new(AtomicUsize::new(0));
        let drops = Arc::new(AtomicUsize::new(0));
        let mut managed = ManagedConnection::new(
            SlowConnection {
                connects: Arc::clone(&connects),
                drops: Arc::clone(&drops),
            },
            1,
            ConnectionCreateOptions::default(),
        );
        managed.begin_lifecycle(ManagedLifecycleOperation::Connect);
        assert!(managed.lifecycle_in_progress());

        let (operation, result) = std::future::poll_fn(|cx| managed.poll_lifecycle(cx)).await;
        assert_eq!(operation, ManagedLifecycleOperation::Connect);
        assert!(result.is_ok());
        assert_eq!(connects.load(Ordering::SeqCst), 1);
        assert!(!managed.lifecycle_in_progress());
        assert_eq!(drops.load(Ordering::SeqCst), 0);
        drop(managed);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn dropping_a_starting_slot_cancels_and_drops_its_connection() {
        let connects = Arc::new(AtomicUsize::new(0));
        let drops = Arc::new(AtomicUsize::new(0));
        let mut connections = ManagedConnections::new();
        let key = "slow".to_owned();
        connections
            .insert_new(
                key.clone(),
                SlowConnection {
                    connects: Arc::clone(&connects),
                    drops: Arc::clone(&drops),
                },
            )
            .unwrap();
        connections
            .get_mut(&key)
            .unwrap()
            .begin_lifecycle(ManagedLifecycleOperation::Connect);
        drop(connections.remove(&key));

        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        assert_eq!(connects.load(Ordering::SeqCst), 0);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
    }
}
