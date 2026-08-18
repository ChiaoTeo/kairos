use std::marker::PhantomData;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;
use std::time::Duration;

use thiserror::Error;
use tokio::sync::{mpsc, oneshot, watch};

use crate::{
    ConfluxActor, ConfluxEvent, ConfluxSystem, Context, ProcessPhase, RestResponseOf, ShutdownMode,
};

type ActorEvent<A> = ConfluxEvent<A, <A as ConfluxActor>::LocalEvent>;

pub(crate) struct EventEnvelope<A: ConfluxActor> {
    pub(crate) event: ActorEvent<A>,
    pub(crate) completed: oneshot::Sender<Option<RestResponseOf<A>>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConfluxConfig {
    pub ingress_capacity: usize,
    pub shutdown_timeout: Duration,
}

impl Default for ConfluxConfig {
    fn default() -> Self {
        Self {
            ingress_capacity: 1_024,
            shutdown_timeout: Duration::from_secs(10),
        }
    }
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum BuildError {
    #[error("ingress capacity must be greater than zero")]
    ZeroIngressCapacity,
    #[error("shutdown timeout must be greater than zero")]
    ZeroShutdownTimeout,
}

pub struct Conflux<A: ConfluxActor> {
    actor: A,
    system: ConfluxSystem,
    sender: mpsc::Sender<EventEnvelope<A>>,
    events: mpsc::Receiver<EventEnvelope<A>>,
    shutdown: watch::Receiver<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
    source_tasks: Vec<tokio::task::JoinHandle<()>>,
    shutdown_timeout: Duration,
}

impl<A: ConfluxActor> Conflux<A> {
    pub fn new(
        actor: A,
        system: ConfluxSystem,
        config: ConfluxConfig,
    ) -> Result<(Self, ConfluxHandle<A>), BuildError> {
        if config.ingress_capacity == 0 {
            return Err(BuildError::ZeroIngressCapacity);
        }
        if config.shutdown_timeout.is_zero() {
            return Err(BuildError::ZeroShutdownTimeout);
        }

        let (sender, events) = mpsc::channel(config.ingress_capacity);
        let (shutdown_sender, shutdown) = watch::channel(None);
        let phase = Arc::new(AtomicU8::new(ProcessPhase::Created as u8));
        let handle = ConfluxHandle {
            sender: sender.clone(),
            shutdown: shutdown_sender,
            phase: Arc::clone(&phase),
            actor: PhantomData,
        };
        Ok((
            Self {
                actor,
                system,
                sender,
                events,
                shutdown,
                phase,
                source_tasks: Vec::new(),
                shutdown_timeout: config.shutdown_timeout,
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
            &mut self.system,
            &mut requested_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
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
            &mut self.system,
            &mut requested_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
        );
        let response = self.actor.handle(envelope.event, &mut context).await?;
        let _ = envelope.completed.send(response);
        Ok(requested_shutdown)
    }

    async fn finish_shutdown(
        mut self,
        mut mode: ShutdownMode,
    ) -> Result<ConfluxOutcome<A>, RunError<A::FatalError>> {
        let deadline = tokio::time::Instant::now() + self.shutdown_timeout;
        self.events.close();

        let mut discarded_inputs = 0;
        if mode == ShutdownMode::Drain {
            loop {
                let envelope = match tokio::time::timeout_at(deadline, self.events.recv()).await {
                    Ok(Some(envelope)) => envelope,
                    Ok(None) => break,
                    Err(_) => {
                        mode = ShutdownMode::Immediate;
                        break;
                    }
                };
                match tokio::time::timeout_at(deadline, self.run_event(envelope)).await {
                    Ok(Ok(Some(ShutdownMode::Immediate))) => {
                        mode = ShutdownMode::Immediate;
                        break;
                    }
                    Ok(Ok(_)) => {}
                    Ok(Err(error)) => {
                        self.set_phase(ProcessPhase::Failed);
                        return Err(RunError::Actor(error));
                    }
                    Err(_) => {
                        discarded_inputs += 1;
                        mode = ShutdownMode::Immediate;
                        break;
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
            &mut self.system,
            &mut ignored_shutdown,
            self.sender.clone(),
            &mut self.source_tasks,
        );
        match tokio::time::timeout_at(deadline, self.actor.stopping(&mut context)).await {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                self.set_phase(ProcessPhase::Failed);
                return Err(RunError::Actor(error));
            }
            Err(_) => mode = ShutdownMode::Immediate,
        }
        for task in self.source_tasks.drain(..) {
            task.abort();
        }

        let phase = match mode {
            ShutdownMode::Drain => ProcessPhase::Stopped,
            ShutdownMode::Immediate => ProcessPhase::Forced,
        };
        self.set_phase(phase);
        Ok(ConfluxOutcome {
            actor: self.actor,
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
    ) -> Result<Option<RestResponseOf<A>>, HandleError<ActorEvent<A>>> {
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

#[derive(PartialEq, Eq)]
pub enum HandleError<E> {
    Closed(E),
    ActorStopped,
}

impl<E> std::fmt::Debug for HandleError<E> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Closed(_) => formatter.write_str("Closed(..)"),
            Self::ActorStopped => formatter.write_str("ActorStopped"),
        }
    }
}

pub struct ConfluxOutcome<A: ConfluxActor> {
    pub actor: A,
    pub system: ConfluxSystem,
    pub phase: ProcessPhase,
    pub discarded_inputs: usize,
}

#[derive(Debug, Error)]
pub enum RunError<E> {
    #[error("Actor failed: {0}")]
    Actor(E),
}

#[cfg(test)]
mod tests {
    use std::convert::Infallible;

    use super::*;
    use crate::{Contract, RestContract};

    struct TestRest;

    impl RestContract for TestRest {
        type Request = i64;
        type Response = i64;
    }

    #[derive(Default)]
    struct TestActor {
        total: i64,
    }

    impl Contract for TestActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for TestActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            match event {
                ConfluxEvent::Rest(value) => Ok(Some(self.total + value)),
                ConfluxEvent::Local(value) => {
                    self.total += value;
                    Ok(None)
                }
                _ => Ok(None),
            }
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn one_handle_serializes_rest_and_event_inputs() {
        let (conflux, handle) = Conflux::new(
            TestActor::default(),
            ConfluxSystem::new(),
            ConfluxConfig {
                ingress_capacity: 8,
                ..ConfluxConfig::default()
            },
        )
        .unwrap();
        tokio::task::LocalSet::new()
            .run_until(async move {
                let process = tokio::task::spawn_local(conflux.run());

                assert_eq!(handle.handle(ConfluxEvent::Local(7)).await.unwrap(), None);
                assert_eq!(
                    handle.handle(ConfluxEvent::Rest(5)).await.unwrap(),
                    Some(12)
                );

                handle.shutdown(ShutdownMode::Drain);
                let outcome = process.await.unwrap().unwrap();
                assert_eq!(outcome.actor.total, 7);
                assert_eq!(outcome.phase, ProcessPhase::Stopped);
                assert_eq!(outcome.discarded_inputs, 0);
            })
            .await;
    }

    #[test]
    fn zero_shutdown_timeout_is_rejected() {
        assert!(matches!(
            Conflux::new(
                TestActor::default(),
                ConfluxSystem::new(),
                ConfluxConfig {
                    shutdown_timeout: Duration::ZERO,
                    ..ConfluxConfig::default()
                },
            ),
            Err(BuildError::ZeroShutdownTimeout)
        ));
    }

    struct StuckStoppingActor;

    impl Contract for StuckStoppingActor {
        type Rest = TestRest;
    }

    impl ConfluxActor for StuckStoppingActor {
        type FatalError = Infallible;
        type LocalEvent = i64;

        async fn handle(
            &mut self,
            _event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<i64>, Self::FatalError> {
            Ok(None)
        }

        async fn stopping(
            &mut self,
            _context: &mut Context<'_, Self>,
        ) -> Result<(), Self::FatalError> {
            std::future::pending().await
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn shutdown_deadline_forces_a_stuck_actor() {
        let (conflux, handle) = Conflux::new(
            StuckStoppingActor,
            ConfluxSystem::new(),
            ConfluxConfig {
                shutdown_timeout: Duration::from_millis(10),
                ..ConfluxConfig::default()
            },
        )
        .unwrap();
        handle.shutdown(ShutdownMode::Drain);
        let outcome = conflux.run().await.unwrap();
        assert_eq!(outcome.phase, ProcessPhase::Forced);
    }
}
