use std::collections::BTreeMap;

use super::super::{MarketApplication, MarketError};
use crate::domain::source::{MarketFeedId, SourceStatus};
use crate::services::actor::{AttachedSource, MarketActor};
use crate::services::source::SourceHandle;
use crate::services::source::messages::SourceInput;

const SOURCE_INPUT_CAPACITY: usize = 4_096;

impl MarketApplication {
    pub fn new(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
    ) -> Result<Self, MarketError> {
        Self::new_with_source_capacity(actor_id, max_dynamic_members, SOURCE_INPUT_CAPACITY)
    }

    pub(crate) fn new_with_source_capacity(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, MarketError> {
        if source_input_capacity == 0 {
            return Err(MarketError::Invalid(
                "market source input capacity must be positive".into(),
            ));
        }
        let actor = MarketActor::new(actor_id, max_dynamic_members, source_input_capacity)
            .map_err(MarketError::Invalid)?;
        Ok(Self {
            actor,
            conflux: super::super::conflux::MarketConfluxState::default(),
        })
    }

    pub(crate) fn restore_with_source_capacity(
        checkpoint: crate::services::actor::ReplayCheckpoint,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, MarketError> {
        if source_input_capacity == 0 {
            return Err(MarketError::Invalid(
                "market source input capacity must be positive".into(),
            ));
        }
        Ok(Self {
            actor: MarketActor::restore(checkpoint, max_dynamic_members, source_input_capacity)
                .map_err(MarketError::Invalid)?,
            conflux: super::super::conflux::MarketConfluxState::default(),
        })
    }

    pub(crate) fn source_input_capacity(&self) -> usize {
        self.actor.source_input_capacity
    }

    pub(crate) fn attach_source(&mut self, handle: SourceHandle) -> Result<(), String> {
        self.actor.register_source(handle.descriptor.clone())?;
        let id = handle.descriptor.id.clone();
        if self.actor.attached_sources.contains_key(&id) {
            return Err(format!("market source already attached: {id}"));
        }
        self.actor.attached_sources.insert(
            id,
            AttachedSource {
                descriptor: handle.descriptor,
                commands: handle.commands,
                inputs: Some(handle.inputs),
                task: Some(handle.task),
                confirmed: BTreeMap::new(),
            },
        );
        Ok(())
    }

    /// Registers a provider source whose connection remains owned and polled by
    /// Conflux. The command sender is deliberately never used; keeping the
    /// existing attached-source shape lets replay/test workers coexist during
    /// the migration without transferring a production connection to a task.
    pub(crate) fn attach_managed_source(
        &mut self,
        descriptor: crate::domain::source::FeedDescriptor,
    ) -> Result<(), String> {
        self.actor.register_source(descriptor.clone())?;
        let id = descriptor.id.clone();
        if self.actor.attached_sources.contains_key(&id) {
            return Err(format!("market source already attached: {id}"));
        }
        let (commands, receiver) = tokio::sync::mpsc::channel(1);
        drop(receiver);
        self.actor.attached_sources.insert(
            id,
            AttachedSource {
                descriptor,
                commands,
                inputs: None,
                task: None,
                confirmed: BTreeMap::new(),
            },
        );
        Ok(())
    }

    pub(crate) async fn next_source_input(&mut self) -> Option<SourceInput> {
        std::future::poll_fn(|context| {
            let source_ids = self
                .actor
                .attached_sources
                .keys()
                .cloned()
                .collect::<Vec<_>>();
            let source_count = source_ids.len();
            if source_count == 0 {
                return std::task::Poll::Pending;
            }
            let mut closed = 0;
            for offset in 0..source_count {
                let index = (self.actor.next_source_input_index + offset) % source_count;
                let source_id = &source_ids[index];
                let source = self
                    .actor
                    .attached_sources
                    .get_mut(source_id)
                    .expect("source id was collected from the same map");
                let Some(inputs) = source.inputs.as_mut() else {
                    continue;
                };
                match std::pin::Pin::new(inputs).poll_recv(context) {
                    std::task::Poll::Ready(Some(input)) => {
                        self.actor.next_source_input_index = (index + 1) % source_count;
                        return std::task::Poll::Ready(Some(input));
                    },
                    std::task::Poll::Ready(None) => closed += 1,
                    std::task::Poll::Pending => {},
                }
            }
            if closed == source_count {
                std::task::Poll::Ready(None)
            } else {
                std::task::Poll::Pending
            }
        })
        .await
    }

    pub(crate) fn take_source_inputs(
        &mut self,
    ) -> Vec<(MarketFeedId, tokio::sync::mpsc::Receiver<SourceInput>)> {
        self.actor
            .attached_sources
            .iter_mut()
            .filter_map(|(id, source)| source.inputs.take().map(|inputs| (id.clone(), inputs)))
            .collect()
    }

    pub fn sources_complete(&self) -> bool {
        if !self.actor.attached_sources.is_empty() {
            return self
                .actor
                .source_states()
                .all(|source| source.status == SourceStatus::Stopped);
        }
        false
    }
}
