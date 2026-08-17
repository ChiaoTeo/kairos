use std::collections::VecDeque;
use std::marker::PhantomData;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;

use thiserror::Error;
use tokio::sync::{mpsc, watch};

use crate::{
    CommitContext, ConfluxActor, ConfluxSystem, Context, ManagedContract, ProcessPhase, RestCallOf,
    ShutdownMode,
};

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

pub struct Conflux<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    actor: A,
    contract: ManagedContract<A::Contract>,
    system: S,
    ingress: mpsc::Receiver<A::Ingress>,
    shutdown: watch::Receiver<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
}

impl<A, S> Conflux<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    pub fn new(
        actor: A,
        contract: ManagedContract<A::Contract>,
        system: S,
        config: RuntimeConfig,
    ) -> Result<(Self, ConfluxHandle<A, S>), BuildError> {
        if config.ingress_capacity == 0 {
            return Err(BuildError::ZeroIngressCapacity);
        }

        let (sender, ingress) = mpsc::channel(config.ingress_capacity);
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
                ingress,
                shutdown,
                phase,
            },
            handle,
        ))
    }

    pub async fn run(mut self) -> Result<ConfluxOutcome<A, S>, RunError<A::FatalError>> {
        self.set_phase(ProcessPhase::Starting);
        let startup_shutdown = match self.run_started().await {
            Ok(mode) => mode,
            Err(error) => {
                self.set_phase(ProcessPhase::Failed);
                return Err(RunError::Actor(error));
            }
        };
        self.set_phase(ProcessPhase::Running);

        let shutdown_mode = if let Some(mode) = startup_shutdown {
            mode
        } else {
            self.run_until_shutdown().await?
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
                ingress = self.ingress.recv() => {
                    let Some(ingress) = ingress else {
                        return Ok(ShutdownMode::Drain);
                    };
                    match self.run_turn(ingress).await {
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
        let mut staged = VecDeque::new();
        let mut requested_shutdown = None;
        {
            let mut context = Context::new(
                &mut self.contract,
                &mut self.system,
                &mut staged,
                &mut requested_shutdown,
            );
            self.actor.started(&mut context).await?;
        }
        self.commit(staged).await?;
        Ok(requested_shutdown)
    }

    async fn run_turn(
        &mut self,
        ingress: A::Ingress,
    ) -> Result<Option<ShutdownMode>, A::FatalError> {
        let mut staged = VecDeque::new();
        let mut requested_shutdown = None;
        {
            let mut context = Context::new(
                &mut self.contract,
                &mut self.system,
                &mut staged,
                &mut requested_shutdown,
            );
            self.actor.handle(ingress, &mut context).await?;
        }
        self.commit(staged).await?;
        Ok(requested_shutdown)
    }

    async fn commit(&mut self, mut staged: VecDeque<A::Output>) -> Result<(), A::FatalError> {
        while let Some(output) = staged.pop_front() {
            let mut context = CommitContext::new(&mut self.contract, &mut self.system);
            self.actor.commit(output, &mut context).await?;
        }
        Ok(())
    }

    async fn finish_shutdown(
        mut self,
        mut mode: ShutdownMode,
    ) -> Result<ConfluxOutcome<A, S>, RunError<A::FatalError>> {
        self.set_phase(ProcessPhase::Quiescing);
        self.ingress.close();

        let mut discarded_inputs = 0;
        if mode == ShutdownMode::Drain {
            self.set_phase(ProcessPhase::DrainingInputs);
            while let Some(ingress) = self.ingress.recv().await {
                match self.run_turn(ingress).await {
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
            while self.ingress.try_recv().is_ok() {
                discarded_inputs += 1;
            }
        }

        self.set_phase(ProcessPhase::Stopping);
        let mut staged = VecDeque::new();
        let mut ignored_shutdown = None;
        {
            let mut context = Context::new(
                &mut self.contract,
                &mut self.system,
                &mut staged,
                &mut ignored_shutdown,
            );
            if let Err(error) = self.actor.stopping(&mut context).await {
                self.set_phase(ProcessPhase::Failed);
                return Err(RunError::Actor(error));
            }
        }
        if let Err(error) = self.commit(staged).await {
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

pub struct ConfluxHandle<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    sender: mpsc::Sender<A::Ingress>,
    shutdown: watch::Sender<Option<ShutdownMode>>,
    phase: Arc<AtomicU8>,
    actor: PhantomData<fn() -> (A, S)>,
}

impl<A, S> Clone for ConfluxHandle<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    fn clone(&self) -> Self {
        Self {
            sender: self.sender.clone(),
            shutdown: self.shutdown.clone(),
            phase: Arc::clone(&self.phase),
            actor: PhantomData,
        }
    }
}

impl<A, S> ConfluxHandle<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    pub async fn notify(&self, ingress: A::Ingress) -> Result<(), NotifyError<A::Ingress>> {
        self.sender
            .send(ingress)
            .await
            .map_err(|error| NotifyError::Closed(error.0))
    }

    pub async fn notify_rest(
        &self,
        call: RestCallOf<A::Contract>,
    ) -> Result<(), NotifyError<A::Ingress>> {
        self.notify(call.into()).await
    }

    pub fn shutdown(&self, mode: ShutdownMode) {
        self.shutdown.send_replace(Some(mode));
    }

    pub fn phase(&self) -> ProcessPhase {
        ProcessPhase::from_u8(self.phase.load(Ordering::Acquire))
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum NotifyError<I> {
    Closed(I),
}

pub struct ConfluxOutcome<A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    pub actor: A,
    pub contract: ManagedContract<A::Contract>,
    pub system: S,
    pub phase: ProcessPhase,
    pub discarded_inputs: usize,
}

#[derive(Debug, Error)]
pub enum RunError<E> {
    #[error("Actor failed: {0}")]
    Actor(E),
}
