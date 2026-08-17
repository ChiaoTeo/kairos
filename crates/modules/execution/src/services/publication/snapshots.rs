use super::*;

pub struct SharedExecutionSnapshotPublisher {
    root: PathBuf,
    slot_size: usize,
    actor_id: String,
    identity: InstanceIdentity,
    publisher: Option<ExecutionViewPublisher>,
    current_publisher: Option<ExecutionViewPublisher>,
    producer_incarnation: u64,
}

impl SharedExecutionSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let path = path.as_ref();
        Ok(Self {
            root: snapshot_root(path),
            slot_size: nonzero_slot_size(slot_size),
            actor_id: actor_id.into(),
            identity,
            publisher: None,
            current_publisher: None,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        let generation = snapshot.generation.get();
        let key = self.key(ExecutionViewKind::ActiveOrders)?;
        let bytes =
            encode_active_orders(&self.actor_id, &self.identity, generation, &key, snapshot)?;
        let metadata = envelope_metadata(self.producer_incarnation, snapshot);
        self.ensure_publisher(key)?
            .publish(metadata, &bytes)
            .map_err(|e| e.to_string())?;

        let current_key = self.key(ExecutionViewKind::CurrentExecution)?;
        let current_bytes = encode_current_execution(
            &self.actor_id,
            &self.identity,
            generation,
            &current_key,
            snapshot,
        )?;
        if self.current_publisher.is_none() {
            self.current_publisher = Some(
                ExecutionViewPublisher::create(&self.root, current_key, self.slot_size)
                    .map_err(|error| error.to_string())?,
            );
        }
        self.current_publisher
            .as_mut()
            .expect("current publisher was inserted")
            .publish(metadata, &current_bytes)
            .map_err(|error| error.to_string())
    }

    fn key(&self, kind: ExecutionViewKind) -> Result<ExecutionViewKey, String> {
        ExecutionViewKey::new(
            self.identity.workspace_id.clone(),
            kind,
            Some(self.identity.launch_id.clone()),
            Some(self.identity.instance_id.clone()),
        )
        .map_err(|e| e.to_string())
    }

    fn ensure_publisher(
        &mut self,
        key: ExecutionViewKey,
    ) -> Result<&mut ExecutionViewPublisher, String> {
        if self.publisher.is_none() {
            self.publisher = Some(
                ExecutionViewPublisher::create(&self.root, key, self.slot_size)
                    .map_err(|e| e.to_string())?,
            );
        }
        Ok(self.publisher.as_mut().expect("publisher was inserted"))
    }
}

pub struct SharedIntentSnapshotPublisher {
    root: PathBuf,
    slot_size: usize,
    actor_id: String,
    identity: InstanceIdentity,
    publisher: Option<ExecutionViewPublisher>,
    producer_incarnation: u64,
}

impl SharedIntentSnapshotPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
    ) -> Result<Self, String> {
        Self::create_with_identity(path, slot_size, actor_id, InstanceIdentity::default())
    }

    pub fn create_with_identity(
        path: impl AsRef<Path>,
        slot_size: usize,
        actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let path = path.as_ref();
        Ok(Self {
            root: snapshot_root(path),
            slot_size: nonzero_slot_size(slot_size),
            actor_id: actor_id.into(),
            identity,
            publisher: None,
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
        })
    }

    pub fn publish(&mut self, snapshot: &ExecutionCurrentView) -> Result<(), String> {
        let generation = snapshot.generation.get();
        let key = ExecutionViewKey::new(
            self.identity.workspace_id.clone(),
            ExecutionViewKind::ActiveIntents,
            Some(self.identity.launch_id.clone()),
            Some(self.identity.instance_id.clone()),
        )
        .map_err(|e| e.to_string())?;
        let bytes =
            encode_active_intents(&self.actor_id, &self.identity, generation, &key, snapshot)?;
        if self.publisher.is_none() {
            self.publisher = Some(
                ExecutionViewPublisher::create(&self.root, key, self.slot_size)
                    .map_err(|e| e.to_string())?,
            );
        }
        self.publisher
            .as_mut()
            .expect("publisher was inserted")
            .publish(
                envelope_metadata(self.producer_incarnation, snapshot),
                &bytes,
            )
            .map_err(|e| e.to_string())
    }
}

fn envelope_metadata(
    producer_incarnation: u64,
    snapshot: &ExecutionCurrentView,
) -> SnapshotEnvelopeMetadata {
    SnapshotEnvelopeMetadata {
        resource_epoch: 1,
        producer_incarnation,
        generation: snapshot.generation.get(),
        applied_event_sequence: snapshot.event_sequence.get(),
        published_at_unix_nanos: now_unix_nanos(),
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}
