use std::collections::HashMap;
use std::hash::Hash;

use thiserror::Error;

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

    pub fn into_connection(self) -> C {
        self.connection
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

/// One named, concrete transport resource such as a typed Aeron stream or
/// contract-owned mmap reader/publisher. `R` is never erased.
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
            .insert(key, ManagedResource::new(create(), revision));
        Ok(EnsureDisposition::Created)
    }

    pub fn get(&self, key: &K) -> Option<&ManagedResource<R>> {
        self.entries.get(key)
    }

    pub fn get_mut(&mut self, key: &K) -> Option<&mut ManagedResource<R>> {
        self.entries.get_mut(key)
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

    pub fn iter_mut(&mut self) -> impl Iterator<Item = (&K, &mut ManagedConnection<C>)> {
        self.entries.iter_mut()
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
