use std::collections::VecDeque;

use crate::{ConfluxActor, ConfluxSystem, ManagedContract, ShutdownMode};

/// Exclusive authority available during one Actor turn.
pub struct Context<'runtime, A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    contract: &'runtime mut ManagedContract<A::Contract>,
    system: &'runtime mut S,
    staged: &'runtime mut VecDeque<A::Output>,
    shutdown: &'runtime mut Option<ShutdownMode>,
}

impl<'runtime, A, S> Context<'runtime, A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    pub(crate) fn new(
        contract: &'runtime mut ManagedContract<A::Contract>,
        system: &'runtime mut S,
        staged: &'runtime mut VecDeque<A::Output>,
        shutdown: &'runtime mut Option<ShutdownMode>,
    ) -> Self {
        Self {
            contract,
            system,
            staged,
            shutdown,
        }
    }

    pub fn contract(&mut self) -> &mut ManagedContract<A::Contract> {
        self.contract
    }

    pub fn clients(&mut self) -> &mut S::Clients {
        self.system.clients_mut()
    }

    pub fn connections(&mut self) -> &mut S::Connections {
        self.system.connections_mut()
    }

    pub fn system(&mut self) -> &mut S {
        self.system
    }

    pub fn stage(&mut self, output: A::Output) {
        self.staged.push_back(output);
    }

    pub fn request_shutdown(&mut self, mode: ShutdownMode) {
        *self.shutdown = Some(mode);
    }
}

/// Exclusive resource access while a staged output is committed.
///
/// This context deliberately has no `stage` method: committing one closed
/// output cannot recursively create an unbounded effect chain.
pub struct CommitContext<'runtime, A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    contract: &'runtime mut ManagedContract<A::Contract>,
    system: &'runtime mut S,
}

impl<'runtime, A, S> CommitContext<'runtime, A, S>
where
    S: ConfluxSystem,
    A: ConfluxActor<S>,
{
    pub(crate) fn new(
        contract: &'runtime mut ManagedContract<A::Contract>,
        system: &'runtime mut S,
    ) -> Self {
        Self { contract, system }
    }

    pub fn contract(&mut self) -> &mut ManagedContract<A::Contract> {
        self.contract
    }

    pub fn clients(&mut self) -> &mut S::Clients {
        self.system.clients_mut()
    }

    pub fn connections(&mut self) -> &mut S::Connections {
        self.system.connections_mut()
    }

    pub fn system(&mut self) -> &mut S {
        self.system
    }
}
