use std::convert::Infallible;

use kairos_conflux::{
    AeronContract, CommitContext, Conflux, ConfluxActor, Context, Contract, ManagedContract,
    RestContract, RuntimeConfig, ServedContract, ShutdownMode, StaticSystem, ViewContract,
};
use tokio::sync::{mpsc, oneshot};

struct ExampleEndpoint;
struct ExampleClient;
struct ExampleRestClient;

struct ExampleRest;

impl RestContract for ExampleRest {
    type Client = ExampleRestClient;
}

#[derive(Debug, PartialEq, Eq)]
struct ExampleViewFrame(u64);

struct ExampleView;

impl ViewContract for ExampleView {
    type Key = &'static str;
    type Frame = ExampleViewFrame;
    type Reader = ();
}

#[derive(Debug, PartialEq, Eq)]
struct ExampleAeronFrame(u64);

struct ExampleAeron;

impl AeronContract for ExampleAeron {
    type Frame = ExampleAeronFrame;
    type Stream = mpsc::Receiver<ExampleAeronFrame>;
}

struct ExampleContract;

impl Contract for ExampleContract {
    type Endpoint = ExampleEndpoint;
    type Client = ExampleClient;
    type Rest = ExampleRest;
    type View = ExampleView;
    type Aeron = ExampleAeron;
}

enum ExampleRestCall {
    Increment {
        amount: u64,
        reply: oneshot::Sender<u64>,
    },
}

#[derive(Default)]
struct ExampleService {
    views: Vec<ExampleViewFrame>,
    aeron: Vec<ExampleAeronFrame>,
}

impl ServedContract for ExampleContract {
    type RestCall = ExampleRestCall;
    type Service = ExampleService;
}

enum ExampleIngress {
    Rest(ExampleRestCall),
}

impl From<ExampleRestCall> for ExampleIngress {
    fn from(call: ExampleRestCall) -> Self {
        Self::Rest(call)
    }
}

enum ExampleOutput {
    Publish {
        value: u64,
        reply: oneshot::Sender<u64>,
    },
}

#[derive(Default)]
struct ExampleActor {
    value: u64,
}

type ExampleSystem = StaticSystem<(), ()>;

impl ConfluxActor<ExampleSystem> for ExampleActor {
    type FatalError = Infallible;
    type Contract = ExampleContract;
    type Ingress = ExampleIngress;
    type Output = ExampleOutput;

    async fn handle(
        &mut self,
        ingress: Self::Ingress,
        context: &mut Context<'_, Self, ExampleSystem>,
    ) -> Result<(), Self::FatalError> {
        let ExampleIngress::Rest(ExampleRestCall::Increment { amount, reply }) = ingress;
        self.value += amount;
        context.stage(ExampleOutput::Publish {
            value: self.value,
            reply,
        });
        Ok(())
    }

    async fn commit(
        &mut self,
        output: Self::Output,
        context: &mut CommitContext<'_, Self, ExampleSystem>,
    ) -> Result<(), Self::FatalError> {
        let ExampleOutput::Publish { value, reply } = output;
        let service = context.contract().service_mut();
        service.views.push(ExampleViewFrame(value));
        service.aeron.push(ExampleAeronFrame(value));
        let _ = reply.send(value);
        Ok(())
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let (runtime, handle) = Conflux::new(
        ExampleActor::default(),
        ManagedContract::new(ExampleService::default(), 1),
        StaticSystem::new((), ()),
        RuntimeConfig::default(),
    )
    .expect("runtime configuration is valid");
    let running = tokio::spawn(runtime.run());

    let (reply, response) = oneshot::channel();
    assert!(handle
        .notify_rest(ExampleRestCall::Increment { amount: 3, reply })
        .await
        .is_ok());
    assert_eq!(response.await.expect("REST reply is committed"), 3);

    handle.shutdown(ShutdownMode::Drain);
    let outcome = running
        .await
        .expect("runtime joins")
        .expect("runtime stops cleanly");
    assert_eq!(outcome.contract.service().views, [ExampleViewFrame(3)]);
    assert_eq!(outcome.contract.service().aeron, [ExampleAeronFrame(3)]);
}
