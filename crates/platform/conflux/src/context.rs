use std::fmt::Display;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use futures_util::{Stream, StreamExt};
use tokio::sync::{mpsc, oneshot};

use crate::process::EventEnvelope;
use crate::{
    ConfluxActor, ConfluxEvent, ConfluxSystem, ContractEvent, IntegrationEvent, ShutdownMode,
    SystemEvent,
};

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

    pub fn request_shutdown(&mut self, mode: ShutdownMode) {
        *self.shutdown = Some(mode);
    }

    pub fn spawn_account_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_account_contract::AccountEventStream,
    ) {
        self.spawn_contract_stream(client, stream, |client, frame| {
            ConfluxEvent::Account(ContractEvent { client, frame })
        });
    }

    pub fn spawn_execution_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_execution_contract::ExecutionEventStream,
    ) {
        self.spawn_contract_stream(client, stream, |client, frame| {
            ConfluxEvent::Execution(ContractEvent { client, frame })
        });
    }

    pub fn spawn_market_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_market_contract::MarketEventStream,
    ) {
        self.spawn_contract_stream(client, stream, |client, frame| {
            ConfluxEvent::Market(ContractEvent { client, frame })
        });
    }

    pub fn spawn_reference_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_reference_contract::ReferenceEventStream,
    ) {
        self.spawn_contract_stream(client, stream, |client, frame| {
            ConfluxEvent::Reference(ContractEvent { client, frame })
        });
    }

    pub fn spawn_risk_events(
        &mut self,
        client: impl Into<String>,
        stream: kairos_risk_contract::RiskEventStream,
    ) {
        self.spawn_contract_stream(client, stream, |client, frame| {
            ConfluxEvent::Risk(ContractEvent { client, frame })
        });
    }

    pub fn spawn_timer(&mut self, name: impl Into<String>, period: Duration) {
        let name = name.into();
        let source = format!("timer:{name}");
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            if period.is_zero() {
                send_source_failure::<A>(&sender, source, "timer period must be positive".into())
                    .await;
                return;
            }
            let mut interval =
                tokio::time::interval_at(tokio::time::Instant::now() + period, period);
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            loop {
                interval.tick().await;
                let fired_at_unix_nanos = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|value| u64::try_from(value.as_nanos()).unwrap_or(u64::MAX))
                    .unwrap_or_default();
                if send_event(
                    &sender,
                    ConfluxEvent::System(SystemEvent::Timer {
                        name: name.clone(),
                        fired_at_unix_nanos,
                    }),
                )
                .await
                .is_err()
                {
                    return;
                }
            }
        }));
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

    /// Moves one multiplexed physical provider connection into a single
    /// supervised reader. This avoids competing mutable consumers for account,
    /// execution, and market channels carried by the same socket.
    pub fn spawn_integration_events<C>(&mut self, connection: impl Into<String>, mut stream: C)
    where
        C: kairos_integration::ParticipantEventStream
            + kairos_integration::ConnectionLifecycleCommand
            + 'static,
    {
        let connection = connection.into();
        let source = format!("integration:{connection}");
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            if let Err(error) =
                kairos_integration::ConnectionLifecycleCommand::connect(&mut stream).await
            {
                send_source_failure::<A>(&sender, source.clone(), error.to_string()).await;
                return;
            }
            send_source_ready::<A>(&sender, source.clone()).await;
            loop {
                match kairos_integration::ParticipantEventStream::next(&mut stream).await {
                    Ok(event) => {
                        if send_event(
                            &sender,
                            ConfluxEvent::Integration(IntegrationEvent {
                                connection: connection.clone(),
                                event,
                            }),
                        )
                        .await
                        .is_err()
                        {
                            return;
                        }
                    }
                    Err(error) => {
                        let _ =
                            kairos_integration::ConnectionLifecycleCommand::disconnect(&mut stream)
                                .await;
                        send_source_failure::<A>(&sender, source, error.to_string()).await;
                        return;
                    }
                }
            }
        }));
    }

    pub fn spawn_integration_account_events<C>(
        &mut self,
        connection: impl Into<String>,
        mut stream: C,
    ) where
        C: kairos_integration::AccountStream
            + kairos_integration::ConnectionLifecycleCommand
            + 'static,
    {
        let connection = connection.into();
        let source = format!("integration:{connection}");
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            if let Err(error) =
                kairos_integration::ConnectionLifecycleCommand::connect(&mut stream).await
            {
                send_source_failure::<A>(&sender, source.clone(), error.to_string()).await;
                return;
            }
            send_source_ready::<A>(&sender, source.clone()).await;
            loop {
                match kairos_integration::AccountStream::next(&mut stream).await {
                    Ok(event) => {
                        if send_event(
                            &sender,
                            ConfluxEvent::Integration(IntegrationEvent {
                                connection: connection.clone(),
                                event: kairos_integration::ExternalParticipantEvent::Account(event),
                            }),
                        )
                        .await
                        .is_err()
                        {
                            return;
                        }
                    }
                    Err(error) => {
                        let _ =
                            kairos_integration::ConnectionLifecycleCommand::disconnect(&mut stream)
                                .await;
                        send_source_failure::<A>(&sender, source, error.to_string()).await;
                        return;
                    }
                }
            }
        }));
    }

    pub fn spawn_integration_execution_events<C>(
        &mut self,
        connection: impl Into<String>,
        mut stream: C,
    ) where
        C: kairos_integration::ExecutionStream
            + kairos_integration::ConnectionLifecycleCommand
            + 'static,
    {
        let connection = connection.into();
        let source = format!("integration:{connection}");
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            if let Err(error) =
                kairos_integration::ConnectionLifecycleCommand::connect(&mut stream).await
            {
                send_source_failure::<A>(&sender, source.clone(), error.to_string()).await;
                return;
            }
            send_source_ready::<A>(&sender, source.clone()).await;
            loop {
                match kairos_integration::ExecutionStream::next(&mut stream).await {
                    Ok(event) => {
                        if send_event(
                            &sender,
                            ConfluxEvent::Integration(IntegrationEvent {
                                connection: connection.clone(),
                                event: kairos_integration::ExternalParticipantEvent::Execution(
                                    event,
                                ),
                            }),
                        )
                        .await
                        .is_err()
                        {
                            return;
                        }
                    }
                    Err(error) => {
                        let _ =
                            kairos_integration::ConnectionLifecycleCommand::disconnect(&mut stream)
                                .await;
                        send_source_failure::<A>(&sender, source, error.to_string()).await;
                        return;
                    }
                }
            }
        }));
    }

    pub fn spawn_integration_market_events<C>(
        &mut self,
        connection: impl Into<String>,
        mut stream: C,
    ) where
        C: kairos_integration::MarketDataStream
            + kairos_integration::ConnectionLifecycleCommand
            + 'static,
    {
        let connection = connection.into();
        let source = format!("integration:{connection}");
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            if let Err(error) =
                kairos_integration::ConnectionLifecycleCommand::connect(&mut stream).await
            {
                send_source_failure::<A>(&sender, source.clone(), error.to_string()).await;
                return;
            }
            send_source_ready::<A>(&sender, source.clone()).await;
            loop {
                match kairos_integration::MarketDataStream::next(&mut stream).await {
                    Ok(event) => {
                        if send_event(
                            &sender,
                            ConfluxEvent::Integration(IntegrationEvent {
                                connection: connection.clone(),
                                event: kairos_integration::ExternalParticipantEvent::Market(event),
                            }),
                        )
                        .await
                        .is_err()
                        {
                            return;
                        }
                    }
                    Err(error) => {
                        let _ =
                            kairos_integration::ConnectionLifecycleCommand::disconnect(&mut stream)
                                .await;
                        send_source_failure::<A>(&sender, source, error.to_string()).await;
                        return;
                    }
                }
            }
        }));
    }

    fn spawn_contract_stream<S, F, E, M>(&mut self, source: impl Into<String>, stream: S, map: M)
    where
        S: Stream<Item = Result<F, E>> + Send + Unpin + 'static,
        F: Send + 'static,
        E: Display + Send + 'static,
        M: Fn(String, F) -> ConfluxEvent<A, A::LocalEvent> + Send + 'static,
    {
        let source = source.into();
        let sender = self.sender.clone();
        self.source_tasks.push(tokio::spawn(async move {
            let mut stream = stream;
            while let Some(result) = stream.next().await {
                let event = match result {
                    Ok(frame) => map(source.clone(), frame),
                    Err(error) => {
                        send_source_failure::<A>(&sender, source.clone(), error.to_string()).await;
                        return;
                    }
                };
                if send_event(&sender, event).await.is_err() {
                    return;
                }
            }
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

async fn send_source_ready<A: ConfluxActor>(
    sender: &mpsc::Sender<EventEnvelope<A>>,
    source: String,
) {
    let _ = send_event(
        sender,
        ConfluxEvent::System(SystemEvent::SourceReady { source }),
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
