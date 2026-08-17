use std::marker::PhantomData;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;

use thiserror::Error;
use tokio::sync::{mpsc, oneshot, watch};

use crate::{
    ConfluxActor, ConfluxEvent, ConfluxSystem, Context, ProcessPhase, RestResponseOf, ShutdownMode,
};

type ActorEvent<A> = ConfluxEvent<<A as ConfluxActor>::Contract, <A as ConfluxActor>::LocalEvent>;

struct EventEnvelope<A: ConfluxActor> {
    event: ActorEvent<A>,
    completed: oneshot::Sender<Option<RestResponseOf<A::Contract>>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RuntimeConfig {
    pub ingress_capacity: usize,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            ingress_capacity: 1_024,
        }
    }
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum BuildError {
    #[error("ingress capacity must be greater than zero")]
    ZeroIngressCapacity,
}

pub struct Conflux<A: ConfluxActor> {
    actor: A,
    contract: A::Contract,
    system: ConfluxSystem,
    events: mpsc::Receiver<EventEnvelope<A>>,
    shutdown: watch::Receiver<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
}

impl<A: ConfluxActor> Conflux<A> {
    pub fn new(
        actor: A,
        contract: A::Contract,
        system: ConfluxSystem,
        config: RuntimeConfig,
    ) -> Result<(Self, ConfluxHandle<A>), BuildError> {
        if config.ingress_capacity == 0 {
            return Err(BuildError::ZeroIngressCapacity);
        }

        let (sender, events) = mpsc::channel(config.ingress_capacity);
        let (shutdown_sender, shutdown) = watch::channel(None);
        let phase = Arc::new(AtomicU8::new(ProcessPhase::Created as u8));
        let handle = ConfluxHandle {
            sender,
            shutdown: shutdown_sender,
            phase: Arc::clone(&phase),
            actor: PhantomData,
        };
        Ok((
            Self {
                actor,
                contract,
                system,
                events,
                shutdown,
                phase,
            },
            handle,
        ))
    }

    pub async fn run(mut self) -> Result<ConfluxOutcome<A>, RunError<A::FatalError>> {
        self.set_phase(ProcessPhase::Starting);
        let startup_shutdown = self.run_started().await.map_err(|error| {
            self.set_phase(ProcessPhase::Failed);
            RunError::Actor(error)
        })?;
        self.set_phase(ProcessPhase::Running);

        let shutdown_mode = match startup_shutdown {
            Some(mode) => mode,
            None => self.run_until_shutdown().await?,
        };
        self.finish_shutdown(shutdown_mode).await
    }

    async fn run_until_shutdown(&mut self) -> Result<ShutdownMode, RunError<A::FatalError>> {
        loop {
            if let Some(mode) = *self.shutdown.borrow() {
                return Ok(mode);
            }

            tokio::select! {
                changed = self.shutdown.changed() => {
                    if changed.is_err() {
                        return Ok(ShutdownMode::Drain);
                    }
                    if let Some(mode) = *self.shutdown.borrow() {
                        return Ok(mode);
                    }
                }
                envelope = self.events.recv() => {
                    let Some(envelope) = envelope else {
                        return Ok(ShutdownMode::Drain);
                    };
                    match self.run_event(envelope).await {
                        Ok(Some(mode)) => return Ok(mode),
                        Ok(None) => {}
                        Err(error) => {
                            self.set_phase(ProcessPhase::Failed);
                            return Err(RunError::Actor(error));
                        }
                    }
                }
            }
        }
    }

    async fn run_started(&mut self) -> Result<Option<ShutdownMode>, A::FatalError> {
        let mut requested_shutdown = None;
        let mut context = Context::new(
            &mut self.contract,
            &mut self.system,
            &mut requested_shutdown,
        );
        self.actor.started(&mut context).await?;
        Ok(requested_shutdown)
    }

    async fn run_event(
        &mut self,
        envelope: EventEnvelope<A>,
    ) -> Result<Option<ShutdownMode>, A::FatalError> {
        let mut requested_shutdown = None;
        let mut context = Context::new(
            &mut self.contract,
            &mut self.system,
            &mut requested_shutdown,
        );
        let response = self.actor.handle(envelope.event, &mut context).await?;
        let _ = envelope.completed.send(response);
        Ok(requested_shutdown)
    }

    async fn finish_shutdown(
        mut self,
        mut mode: ShutdownMode,
    ) -> Result<ConfluxOutcome<A>, RunError<A::FatalError>> {
        self.events.close();

        let mut discarded_inputs = 0;
        if mode == ShutdownMode::Drain {
            while let Some(envelope) = self.events.recv().await {
                match self.run_event(envelope).await {
                    Ok(Some(ShutdownMode::Immediate)) => {
                        mode = ShutdownMode::Immediate;
                        break;
                    }
                    Ok(_) => {}
                    Err(error) => {
                        self.set_phase(ProcessPhase::Failed);
                        return Err(RunError::Actor(error));
                    }
                }
            }
        }
        if mode == ShutdownMode::Immediate {
            while self.events.try_recv().is_ok() {
                discarded_inputs += 1;
            }
        }

        self.set_phase(ProcessPhase::Stopping);
        let mut ignored_shutdown = None;
        let mut context = Context::new(
            &mut self.contract,
            &mut self.system,
            &mut ignored_shutdown,
        );
        if let Err(error) = self.actor.stopping(&mut context).await {
            self.set_phase(ProcessPhase::Failed);
            return Err(RunError::Actor(error));
        }

        let phase = match mode {
            ShutdownMode::Drain => ProcessPhase::Stopped,
            ShutdownMode::Immediate => ProcessPhase::Forced,
        };
        self.set_phase(phase);
        Ok(ConfluxOutcome {
            actor: self.actor,
            contract: self.contract,
            system: self.system,
            phase,
            discarded_inputs,
        })
    }

    fn set_phase(&self, phase: ProcessPhase) {
        self.phase.store(phase as u8, Ordering::Release);
    }
}

pub struct ConfluxHandle<A: ConfluxActor> {
    sender: mpsc::Sender<EventEnvelope<A>>,
    shutdown: watch::Sender<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
    actor: PhantomData<fn() -> A>,
}

impl<A: ConfluxActor> Clone for ConfluxHandle<A> {
    fn clone(&self) -> Self {
        Self {
            sender: self.sender.clone(),
            shutdown: self.shutdown.clone(),
            phase: Arc::clone(&self.phase),
            actor: PhantomData,
        }
    }
}

impl<A: ConfluxActor> ConfluxHandle<A> {
    /// The single entry point for every event source.
    pub async fn handle(
        &self,
        event: ActorEvent<A>,
    ) -> Result<Option<RestResponseOf<A::Contract>>, HandleError<ActorEvent<A>>> {
        let (completed, response) = oneshot::channel();
        self.sender
            .send(EventEnvelope { event, completed })
            .await
            .map_err(|error| HandleError::Closed(error.0.event))?;
        response.await.map_err(|_| HandleError::ActorStopped)
    }

    pub fn shutdown(&self, mode: ShutdownMode) {
        self.shutdown.send_replace(Some(mode));
    }

    pub fn phase(&self) -> ProcessPhase {
        ProcessPhase::from_u8(self.phase.load(Ordering::Acquire))
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum HandleError<E> {
    Closed(E),
    ActorStopped,
}

pub struct ConfluxOutcome<A: ConfluxActor> {
    pub actor: A,
    pub contract: A::Contract,
    pub system: ConfluxSystem,
    pub phase: ProcessPhase,
    pub discarded_inputs: usize,
}

#[derive(Debug, Error)]
pub enum RunError<E> {
    #[error("Actor failed: {0}")]
    Actor(E),
}
