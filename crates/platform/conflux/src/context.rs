use std::time::Duration;

use tokio::sync::{mpsc, oneshot};

use crate::process::EventEnvelope;
use crate::{ConfluxActor, ConfluxEvent, ConfluxSystem, ShutdownMode, SystemEvent};

/// Exclusive authority available during one Actor event.
pub struct Context<'process, A: ConfluxActor> {
    system: &'process mut ConfluxSystem,
    shutdown: &'process mut Option<ShutdownMode>,
    sender: mpsc::Sender<EventEnvelope<A>>,
    source_tasks: &'process mut Vec<tokio::task::JoinHandle<()>>,
}

impl<'process, A: ConfluxActor> Context<'process, A> {
    pub(crate) fn new(
        system: &'process mut ConfluxSystem,
        shutdown: &'process mut Option<ShutdownMode>,
        sender: mpsc::Sender<EventEnvelope<A>>,
        source_tasks: &'process mut Vec<tokio::task::JoinHandle<()>>,
    ) -> Self {
        Self {
            system,
            shutdown,
            sender,
            source_tasks,
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

    pub fn reference_client(
        &mut self,
        key: &str,
    ) -> Option<&mut kairos_reference_contract::ReferenceClient> {
        self.system.reference_client_mut(key)
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
        self.system
            .account_event_streams
            .ensure_with(client.clone(), 1, || stream)?;
        self.system
            .account_event_streams
            .get_mut(&client)
            .expect("registered Account event stream")
            .set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_execution_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_execution_contract::ExecutionEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        self.system
            .execution_event_streams
            .ensure_with(client.clone(), 1, || stream)?;
        self.system
            .execution_event_streams
            .get_mut(&client)
            .expect("registered Execution event stream")
            .set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_market_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_market_contract::MarketEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        self.system
            .market_event_streams
            .ensure_with(client.clone(), 1, || stream)?;
        self.system
            .market_event_streams
            .get_mut(&client)
            .expect("registered Market event stream")
            .set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_reference_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_reference_contract::ReferenceEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        self.system
            .reference_event_streams
            .ensure_with(client.clone(), 1, || stream)?;
        self.system
            .reference_event_streams
            .get_mut(&client)
            .expect("registered Reference event stream")
            .set_state(crate::ResourceState::Ready);
        Ok(())
    }

    pub fn register_risk_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_risk_contract::RiskEventStream,
    ) -> Result<(), crate::ResourceError> {
        let client = client.into();
        self.system
            .risk_event_streams
            .ensure_with(client.clone(), 1, || stream)?;
        self.system
            .risk_event_streams
            .get_mut(&client)
            .expect("registered Risk event stream")
            .set_state(crate::ResourceState::Ready);
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

    /// Moves a module-owned receiver into Conflux supervision and wraps every
    /// value in the Actor's one global event enum. The value remains concrete;
    /// this is only lifecycle and backpressure plumbing.
    pub fn spawn_local_receiver(
        &mut self,
        name: impl Into<String>,
        receiver: mpsc::Receiver<A::LocalEvent>,
    ) {
        self.spawn_local_receiver_map(name, receiver, |event| event);
    }

    pub fn spawn_local_receiver_map<T, M>(
        &mut self,
        name: impl Into<String>,
        mut receiver: mpsc::Receiver<T>,
        map: M,
    ) where
        T: Send + 'static,
        M: Fn(T) -> A::LocalEvent + Send + 'static,
    {
        let source = format!("local:{}", name.into());
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            while let Some(event) = receiver.recv().await {
                if send_event(&sender, ConfluxEvent::Local(map(event)))
                    .await
                    .is_err()
                {
                    return;
                }
            }
            send_source_failure::<A>(&sender, source, "local event source closed".into()).await;
        }));
    }
}

async fn send_source_failure<A: ConfluxActor>(
    sender: &mpsc::Sender<EventEnvelope<A>>,
    source: String,
    error: String,
) {
    let _ = send_event(
        sender,
        ConfluxEvent::System(SystemEvent::SourceFailed { source, error }),
    )
    .await;
}

async fn send_event<A: ConfluxActor>(
    sender: &mpsc::Sender<EventEnvelope<A>>,
    event: ConfluxEvent<A, A::LocalEvent>,
) -> Result<(), ()> {
    let (completed, response) = oneshot::channel();
    drop(response);
    sender
        .send(EventEnvelope { event, completed })
        .await
        .map_err(|_| ())
}

#[cfg(test)]
mod tests {
    use std::convert::Infallible;

    use kairos_market_contract::{MarketViewKey, MarketViewKind};

    use super::*;
    use crate::process::EventEnvelope;
    use crate::{Contract, RestContract};

    struct NoRest;
    impl RestContract for NoRest {
        type Request = ();
        type Response = ();
    }

    struct TestActor;
    impl Contract for TestActor {
        type Rest = NoRest;
    }
    impl ConfluxActor for TestActor {
        type FatalError = Infallible;
        type LocalEvent = Infallible;

        async fn handle(
            &mut self,
            _event: ConfluxEvent<Self, Self::LocalEvent>,
            _context: &mut Context<'_, Self>,
        ) -> Result<Option<()>, Self::FatalError> {
            Ok(None)
        }
    }

    #[test]
    fn handle_context_can_declare_a_dynamic_typed_view() {
        let directory = tempfile::tempdir().unwrap();
        let mut system = ConfluxSystem::new();
        let mut shutdown = None;
        let (sender, _receiver) = mpsc::channel::<EventEnvelope<TestActor>>(1);
        let mut source_tasks = Vec::new();
        let mut context = Context::new(&mut system, &mut shutdown, sender, &mut source_tasks);
        let key =
            MarketViewKey::new("scope", "source", MarketViewKind::Quote, None::<String>).unwrap();

        let resource_key = key.canonical_key();
        let root = directory.path().to_path_buf();
        let resource_key = context
            .declare_output(
                |system| &mut system.market_view_publishers,
                resource_key,
                7,
                move || {
                    kairos_market_contract::MarketViewPublisher::create(root, key, 4_096)
                        .map_err(|error| crate::OutputBindingError::Create(error.to_string()))
                },
            )
            .unwrap();

        let resource = system.market_view_publishers.get(&resource_key).unwrap();
        assert_eq!(resource.revision(), 7);
        assert_eq!(resource.state(), crate::ResourceState::Ready);
        assert!(std::fs::read_dir(directory.path()).unwrap().any(|entry| {
            entry.is_ok_and(|entry| entry.path().to_string_lossy().ends_with(".e1.mmap"))
        }));
    }
}
