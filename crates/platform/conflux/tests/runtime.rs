use std::fmt;
use std::sync::{Arc, Mutex};

use kairos_conflux::{
    CommitContext, Conflux, ConfluxActor, ConfluxSystem, Context, Contract, ManagedClients,
    ManagedConnections, ManagedContract, NoAeron, NoViews, ProcessPhase, RestContract,
    RuntimeConfig, ServedContract, ShutdownMode, StaticSystem,
};
use tokio::sync::oneshot;

struct ExecutionRest;

impl RestContract for ExecutionRest {
    type Client = ();
}

struct Execution;

impl Contract for Execution {
    type Endpoint = ();
    type Rest = ExecutionRest;
    type View = NoViews;
    type Aeron = NoAeron;
    type Client = ();
}

impl ServedContract for Execution {
    type RestCall = ExecutionCall;
    type Service = Arc<Mutex<Vec<u64>>>;
}

struct ReferenceRest;

impl RestContract for ReferenceRest {
    type Client = ReferenceClient;
}

struct Reference;

impl Contract for Reference {
    type Endpoint = ();
    type Rest = ReferenceRest;
    type View = NoViews;
    type Aeron = NoAeron;
    type Client = ReferenceClient;
}

struct ReferenceClient(&'static str);
struct BinanceSpot(&'static str);

struct AllClients {
    references: ManagedClients<&'static str, Reference>,
}

struct AllConnections {
    binance_spot: ManagedConnections<&'static str, BinanceSpot>,
}

type TestSystem = StaticSystem<AllClients, AllConnections>;

enum ExecutionCall {
    Add {
        amount: u64,
        reply: oneshot::Sender<u64>,
    },
}

enum ExecutionIngress {
    Rest(ExecutionCall),
    ProviderFact(u64),
    Fail,
}

impl From<ExecutionCall> for ExecutionIngress {
    fn from(call: ExecutionCall) -> Self {
        Self::Rest(call)
    }
}

enum ExecutionOutput {
    Publish {
        value: u64,
        reply: Option<oneshot::Sender<u64>>,
    },
}

#[derive(Debug)]
struct Fatal;

impl fmt::Display for Fatal {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("fatal test error")
    }
}

impl std::error::Error for Fatal {}

#[derive(Default)]
struct ExecutionActor {
    value: u64,
}

impl ConfluxActor<TestSystem> for ExecutionActor {
    type FatalError = Fatal;
    type Contract = Execution;
    type Ingress = ExecutionIngress;
    type Output = ExecutionOutput;

    async fn started(
        &mut self,
        context: &mut Context<'_, Self, TestSystem>,
    ) -> Result<(), Self::FatalError> {
        context
            .clients()
            .references
            .ensure_with("reference-main", 1, || ReferenceClient("reference-main"))
            .expect("reference revision is valid");
        context
            .connections()
            .binance_spot
            .ensure_with("orders", 1, || BinanceSpot("orders"))
            .expect("connection revision is valid");
        Ok(())
    }

    async fn handle(
        &mut self,
        ingress: Self::Ingress,
        context: &mut Context<'_, Self, TestSystem>,
    ) -> Result<(), Self::FatalError> {
        match ingress {
            ExecutionIngress::Rest(ExecutionCall::Add { amount, reply }) => {
                self.value += amount;
                context.stage(ExecutionOutput::Publish {
                    value: self.value,
                    reply: Some(reply),
                });
            }
            ExecutionIngress::ProviderFact(amount) => {
                let reference_name = context
                    .clients()
                    .references
                    .get(&"reference-main")
                    .expect("reference client exists")
                    .client()
                    .0;
                let connection_name = context
                    .connections()
                    .binance_spot
                    .get(&"orders")
                    .expect("connection exists")
                    .connection()
                    .0;
                assert_eq!(reference_name, "reference-main");
                assert_eq!(connection_name, "orders");
                self.value += amount;
                context.stage(ExecutionOutput::Publish {
                    value: self.value,
                    reply: None,
                });
            }
            ExecutionIngress::Fail => {
                context.stage(ExecutionOutput::Publish {
                    value: 999,
                    reply: None,
                });
                return Err(Fatal);
            }
        }
        Ok(())
    }

    async fn commit(
        &mut self,
        output: Self::Output,
        context: &mut CommitContext<'_, Self, TestSystem>,
    ) -> Result<(), Self::FatalError> {
        let ExecutionOutput::Publish { value, reply } = output;
        context
            .contract()
            .service()
            .lock()
            .expect("service lock is available")
            .push(value);
        if let Some(reply) = reply {
            let _ = reply.send(value);
        }
        Ok(())
    }
}

fn runtime(
    publication: Arc<Mutex<Vec<u64>>>,
) -> (
    Conflux<ExecutionActor, TestSystem>,
    kairos_conflux::ConfluxHandle<ExecutionActor, TestSystem>,
) {
    let clients = AllClients {
        references: ManagedClients::new(),
    };
    let connections = AllConnections {
        binance_spot: ManagedConnections::new(),
    };
    Conflux::new(
        ExecutionActor::default(),
        ManagedContract::new(publication, 1),
        StaticSystem::new(clients, connections),
        RuntimeConfig {
            ingress_capacity: 8,
        },
    )
    .expect("runtime configuration is valid")
}

#[tokio::test]
async fn actor_uses_system_resources_on_demand_and_commits_in_order() {
    let publication = Arc::new(Mutex::new(Vec::new()));
    let (runtime, handle) = runtime(Arc::clone(&publication));
    let run = tokio::spawn(runtime.run());

    let (reply, response) = oneshot::channel();
    assert!(handle
        .notify_rest(ExecutionCall::Add { amount: 2, reply })
        .await
        .is_ok());
    assert_eq!(response.await.expect("reply is delivered after commit"), 2);
    assert!(handle
        .notify(ExecutionIngress::ProviderFact(3))
        .await
        .is_ok());
    handle.shutdown(ShutdownMode::Drain);

    let outcome = run
        .await
        .expect("runtime task joins")
        .expect("runtime stops cleanly");
    assert_eq!(outcome.phase, ProcessPhase::Stopped);
    assert_eq!(outcome.actor.value, 5);
    assert_eq!(
        publication
            .lock()
            .expect("publication lock is available")
            .as_slice(),
        &[2, 5]
    );
    assert_eq!(outcome.system.clients().references.len(), 1);
    assert_eq!(outcome.system.connections().binance_spot.len(), 1);
}

#[tokio::test]
async fn fatal_turn_discards_its_staged_output() {
    let publication = Arc::new(Mutex::new(Vec::new()));
    let (runtime, handle) = runtime(Arc::clone(&publication));
    let run = tokio::spawn(runtime.run());

    assert!(handle.notify(ExecutionIngress::Fail).await.is_ok());
    assert!(run.await.expect("runtime task joins").is_err());
    assert!(publication
        .lock()
        .expect("publication lock is available")
        .is_empty());
}
