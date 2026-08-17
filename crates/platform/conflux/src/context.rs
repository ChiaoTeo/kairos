use crate::{ConfluxActor, ConfluxSystem, ShutdownMode};

/// Exclusive authority available during one Actor event.
pub struct Context<'runtime, A: ConfluxActor> {
    contract: &'runtime mut A::Contract,
    system: &'runtime mut ConfluxSystem,
    shutdown: &'runtime mut Option<ShutdownMode>,
}

impl<'runtime, A: ConfluxActor> Context<'runtime, A> {
    pub(crate) fn new(
        contract: &'runtime mut A::Contract,
        system: &'runtime mut ConfluxSystem,
        shutdown: &'runtime mut Option<ShutdownMode>,
    ) -> Self {
        Self {
            contract,
            system,
            shutdown,
        }
    }

    pub fn contract(&mut self) -> &mut A::Contract {
        self.contract
    }

    pub fn system(&mut self) -> &mut ConfluxSystem {
        self.system
    }

    pub fn request_shutdown(&mut self, mode: ShutdownMode) {
        *self.shutdown = Some(mode);
    }
}
