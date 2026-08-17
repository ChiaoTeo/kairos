use std::collections::HashMap;
use std::hash::Hash;

use thiserror::Error;

use crate::{Contract, ServedContract};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceState {
    Created,
    Starting,
    Ready,
    Degraded,
    Failed,
    Stopping,
    Stopped,
}

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
}

/// The one outward service owned by a Conflux process.
pub struct ManagedContract<C: ServedContract> {
    service: C::Service,
    revision: u64,
    epoch: u64,
    state: ResourceState,
}

impl<C: ServedContract> ManagedContract<C> {
    pub fn new(service: C::Service, revision: u64) -> Self {
        Self {
            service,
            revision,
            epoch: 0,
            state: ResourceState::Created,
        }
    }

    pub fn service(&self) -> &C::Service {
        &self.service
    }

    pub fn service_mut(&mut self) -> &mut C::Service {
        &mut self.service
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

    pub fn replace(
        &mut self,
        revision: u64,
        service: C::Service,
    ) -> Result<C::Service, ResourceError> {
        validate_new_revision(self.revision, revision)?;
        self.epoch = next_epoch(self.epoch)?;
        self.revision = revision;
        self.state = ResourceState::Created;
        Ok(std::mem::replace(&mut self.service, service))
    }

    pub fn into_service(self) -> C::Service {
        self.service
    }
}

pub struct ManagedClient<C: Contract> {
    client: C::Client,
    revision: u64,
    epoch: u64,
    state: ResourceState,
}

impl<C: Contract> ManagedClient<C> {
    fn new(client: C::Client, revision: u64) -> Self {
        Self {
            client,
            revision,
            epoch: 0,
            state: ResourceState::Created,
        }
    }

    pub fn client(&self) -> &C::Client {
        &self.client
    }

    pub fn client_mut(&mut self) -> &mut C::Client {
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

    fn replace(&mut self, revision: u64, client: C::Client) -> Result<(), ResourceError> {
        validate_new_revision(self.revision, revision)?;
        self.epoch = next_epoch(self.epoch)?;
        self.revision = revision;
        self.client = client;
        self.state = ResourceState::Created;
        Ok(())
    }
}

pub struct ManagedClients<K, C: Contract> {
    entries: HashMap<K, ManagedClient<C>>,
}

impl<K, C: Contract> Default for ManagedClients<K, C> {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
        }
    }
}

impl<K, C> ManagedClients<K, C>
where
    K: Eq + Hash,
    C: Contract,
{
    pub fn new() -> Self {
        Self::default()
    }

    pub fn ensure_with(
        &mut self,
        key: K,
        revision: u64,
        create: impl FnOnce() -> C::Client,
    ) -> Result<EnsureDisposition, ResourceError> {
        if let Some(entry) = self.entries.get_mut(&key) {
            if revision < entry.revision {
                return Err(ResourceError::StaleRevision {
                    current: entry.revision,
                    received: revision,
                });
            }
            if revision == entry.revision {
                return Ok(EnsureDisposition::Existing);
            }
            entry.replace(revision, create())?;
            return Ok(EnsureDisposition::Replaced);
        }

        self.entries
            .insert(key, ManagedClient::new(create(), revision));
        Ok(EnsureDisposition::Created)
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

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

pub struct ManagedConnection<C> {
    connection: C,
    revision: u64,
    epoch: u64,
    state: ResourceState,
}

impl<C> ManagedConnection<C> {
    fn new(connection: C, revision: u64) -> Self {
        Self {
            connection,
            revision,
            epoch: 0,
            state: ResourceState::Created,
        }
    }

    pub fn connection(&self) -> &C {
        &self.connection
    }

    pub fn connection_mut(&mut self) -> &mut C {
        &mut self.connection
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

    fn replace(&mut self, revision: u64, connection: C) -> Result<(), ResourceError> {
        validate_new_revision(self.revision, revision)?;
        self.epoch = next_epoch(self.epoch)?;
        self.revision = revision;
        self.connection = connection;
        self.state = ResourceState::Created;
        Ok(())
    }
}

pub struct ManagedConnections<K, C> {
    entries: HashMap<K, ManagedConnection<C>>,
}

impl<K, C> Default for ManagedConnections<K, C> {
    fn default() -> Self {
        Self {
            entries: HashMap::new(),
        }
    }
}

impl<K, C> ManagedConnections<K, C>
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
        if let Some(entry) = self.entries.get_mut(&key) {
            if revision < entry.revision {
                return Err(ResourceError::StaleRevision {
                    current: entry.revision,
                    received: revision,
                });
            }
            if revision == entry.revision {
                return Ok(EnsureDisposition::Existing);
            }
            entry.replace(revision, create())?;
            return Ok(EnsureDisposition::Replaced);
        }

        self.entries
            .insert(key, ManagedConnection::new(create(), revision));
        Ok(EnsureDisposition::Created)
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

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
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
