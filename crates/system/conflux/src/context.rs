use std::marker::PhantomData;
use std::time::Duration;

use tokio::sync::{mpsc, oneshot};

use crate::process::EventEnvelope;
use crate::{ConfluxActor, ConfluxEvent, ConfluxSystem, ShutdownMode};

/// Exclusive authority available during one Actor event.
pub struct Context<'process, A: ConfluxActor> {
    system: &'process mut ConfluxSystem,
    shutdown: &'process mut Option<ShutdownMode>,
    source_tasks: &'process mut Vec<tokio::task::JoinHandle<()>>,
    event_sender: &'process mpsc::Sender<EventEnvelope<A>>,
    actor: PhantomData<fn() -> A>,
}

impl<'process, A: ConfluxActor> Context<'process, A> {
    pub(crate) fn new(
        system: &'process mut ConfluxSystem,
        shutdown: &'process mut Option<ShutdownMode>,
        source_tasks: &'process mut Vec<tokio::task::JoinHandle<()>>,
        event_sender: &'process mpsc::Sender<EventEnvelope<A>>,
    ) -> Self {
        Self {
            system,
            shutdown,
            source_tasks,
            event_sender,
            actor: PhantomData,
        }
    }

    pub fn system(&mut self) -> &mut ConfluxSystem {
        self.system
    }

    pub fn connections(&mut self) -> crate::system::ConnectionCollections<'_> {
        self.system.connections()
    }

    pub fn outputs(&mut self) -> crate::OutputCollections<'_> {
        self.system.outputs()
    }

    pub fn account_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_account_contract::AccountClient> {
        self.system.account_client_mut(key)
    }

    pub fn capital_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_capital_contract::CapitalClient> {
        self.system.capital_client_mut(key)
    }

    pub fn execution_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_execution_contract::ExecutionClient> {
        self.system.execution_client_mut(key)
    }

    pub fn market_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_market_contract::MarketClient> {
        self.system.market_client_mut(key)
    }

    pub fn reference_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_reference_contract::ReferenceClient> {
        self.system.reference_client_mut(key)
    }

    pub fn risk_client(&mut self, key: &str) -> Option<&mut kairos_risk_contract::RiskClient> {
        self.system.risk_client_mut(key)
    }

    pub fn request_shutdown(&mut self, mode: ShutdownMode) {
        *self.shutdown = Some(mode);
    }

    pub fn register_account_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_account_contract::AccountEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) = self
            .system
            .account_event_streams
            .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_capital_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_capital_contract::CapitalEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) = self
            .system
            .capital_event_streams
            .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_execution_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_execution_contract::ExecutionEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) =
            self.system
                .execution_event_streams
                .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_market_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_market_contract::MarketEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) = self
            .system
            .market_event_streams
            .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_reference_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_reference_contract::ReferenceEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) =
            self.system
                .reference_event_streams
                .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_risk_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_risk_contract::RiskEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        let (_, stream) = self
            .system
            .risk_event_streams
            .ensure_with_entry(client, 1, || stream)?;
        stream.set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn spawn_timer(&mut self, name: impl Into<String>, period: Duration) {
        self.system.register_timer(name.into(), period);
    }

    /// Places a module-owned technical worker under the Conflux lifecycle.
    /// The worker must receive its graceful-stop signal from the Actor; any
    /// worker still running after `stopping` returns is aborted by Conflux.
    pub fn spawn_task<F>(&mut self, task: F)
    where
        F: std::future::Future<Output = ()> + Send + 'static,
    {
        self.source_tasks.push(tokio::spawn(task));
    }

    pub fn spawn_local_events(&mut self, receiver: mpsc::Receiver<A::LocalEvent>) {
        self.spawn_mapped_local_events(receiver, |event| event);
    }

    pub fn spawn_mapped_local_events<E, F>(&mut self, mut receiver: mpsc::Receiver<E>, map: F)
    where
        E: Send + 'static,
        F: Fn(E) -> A::LocalEvent + Send + 'static,
    {
        let sender = self.event_sender.clone();
        self.spawn_task(async move {
            while let Some(event) = receiver.recv().await {
                let (completed, response) = oneshot::channel();
                let envelope = EventEnvelope {
                    event: ConfluxEvent::Local(map(event)),
                    completed,
                };
                if sender.send(envelope).await.is_err() {
                    break;
                }
                if response.await.is_err() {
                    break;
                }
            }
        });
    }
}
