use std::collections::BTreeMap;

use super::super::{MarketApplication, MarketError};
use crate::domain::source::{SourceId, SourceStatus};
use crate::services::actor::{AttachedSource, MarketActor};
use crate::services::source::messages::SourceInput;
use crate::services::source::SourceHandle;

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
        Ok(Self { actor })
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
                inputs: handle.inputs,
                task: Some(handle.task),
                confirmed: BTreeMap::new(),
            },
        );
        Ok(())
    }

    pub(crate) fn take_source_handle(
        &mut self,
        source_id: &SourceId,
    ) -> Result<SourceHandle, String> {
        self.actor.take_source_handle(source_id)
    }

    pub(crate) fn has_sources(&self) -> bool {
        !self.actor.attached_sources.is_empty()
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
                match std::pin::Pin::new(&mut source.inputs).poll_recv(context) {
                    std::task::Poll::Ready(Some(input)) => {
                        self.actor.next_source_input_index = (index + 1) % source_count;
                        return std::task::Poll::Ready(Some(input));
                    }
                    std::task::Poll::Ready(None) => closed += 1,
                    std::task::Poll::Pending => {}
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

    pub fn sources_complete(&self) -> bool {
        if !self.actor.attached_sources.is_empty() {
            return self
                .current_view()
                .sources
                .values()
                .all(|source| source.status == SourceStatus::Stopped);
        }
        false
    }
}
