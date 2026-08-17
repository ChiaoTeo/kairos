use std::convert::Infallible;

use kairos_conflux::{
    Conflux, ConfluxActor, ConfluxEvent, ConfluxSystem, Context, Contract, RestContract,
    RuntimeConfig, ShutdownMode,
};

struct ExampleRest;

impl RestContract for ExampleRest {
    type Request = ExampleRestRequest;
    type Response = u64;
}

#[derive(Debug, PartialEq, Eq)]
struct ExampleViewFrame(u64);

#[derive(Debug, PartialEq, Eq)]
struct ExampleAeronFrame(u64);

#[derive(Default)]
struct ExampleContract {
    views: Vec<ExampleViewFrame>,
    aeron: Vec<ExampleAeronFrame>,
}

impl Contract for ExampleContract {
    type Rest = ExampleRest;
}

enum ExampleRestRequest {
    Increment { amount: u64 },
}

#[derive(Default)]
struct ExampleActor {
    value: u64,
}

impl ConfluxActor for ExampleActor {
    type FatalError = Infallible;
    type Contract = ExampleContract;
    type LocalEvent = Infallible;

    async fn handle(
        &mut self,
        event: ConfluxEvent<Self::Contract, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<u64>, Self::FatalError> {
        match event {
            ConfluxEvent::Rest(ExampleRestRequest::Increment { amount }) => {
                self.value += amount;
                let contract = context.contract();
                contract.views.push(ExampleViewFrame(self.value));
                contract.aeron.push(ExampleAeronFrame(self.value));
                Ok(Some(self.value))
            }
            _ => Ok(None),
        }
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let (runtime, handle) = Conflux::new(
        ExampleActor::default(),
        ExampleContract::default(),
        ConfluxSystem::new(),
        RuntimeConfig::default(),
    )
    .expect("runtime configuration is valid");
    let running = tokio::spawn(runtime.run());

    let response = handle
        .handle(ConfluxEvent::Rest(ExampleRestRequest::Increment {
            amount: 3,
        }))
        .await
        .expect("event is handled");
    assert_eq!(response, Some(3));

    handle.shutdown(ShutdownMode::Drain);
    let outcome = running
        .await
        .expect("runtime joins")
        .expect("runtime stops cleanly");
    assert_eq!(outcome.contract.views, [ExampleViewFrame(3)]);
    assert_eq!(outcome.contract.aeron, [ExampleAeronFrame(3)]);
}
