//! Blocking provider feed worker.
//!
//! Provider connections are allowed to block here.  The worker owns the
//! concrete feed and sends normalized values back to the Market actor.  The
//! actor remains the only owner of market business state.

use std::collections::BTreeMap;
use std::sync::mpsc::{self, Receiver, Sender, SyncSender, TryRecvError, TrySendError};
use std::thread;
use std::time::{Duration, Instant};

use super::feed::{MarketFeed, MarketOrderBookUpdate};
use crate::domain::freshness::FeedStatus;
use crate::domain::market::MarketDescriptor;
use crate::domain::observations::MarketObservation;
use crate::domain::subscriptions::SubscriptionId;

enum Command {
    Subscribe {
        id: SubscriptionId,
        market: MarketDescriptor,
        result: Sender<Result<SubscriptionId, String>>,
    },
    Unsubscribe(SubscriptionId),
    ResyncOrderBook(String),
    Recover,
}

enum Event {
    Status(FeedStatus),
    Batch {
        observations: Vec<MarketObservation>,
        orderbooks: Vec<MarketOrderBookUpdate>,
    },
    Error(String),
}

pub struct MarketFeedWorker {
    commands: SyncSender<Command>,
    events: Receiver<Event>,
    status: FeedStatus,
    orderbooks: Vec<MarketOrderBookUpdate>,
    observations: Vec<MarketObservation>,
    next_subscription_id: u64,
}

impl MarketFeedWorker {
    pub fn start(mut feed: Box<dyn MarketFeed>, poll_interval: Duration) -> Self {
        let (command_sender, command_receiver) = mpsc::sync_channel(1024);
        let (event_sender, event_receiver) = mpsc::sync_channel(4096);
        let interval = poll_interval.max(Duration::from_millis(1));

        thread::Builder::new()
            .name("market-feed-worker".into())
            .spawn(move || run_worker(&mut *feed, command_receiver, event_sender, interval))
            .expect("market feed worker thread must start");

        Self {
            commands: command_sender,
            events: event_receiver,
            status: FeedStatus::Disconnected,
            orderbooks: Vec::new(),
            observations: Vec::new(),
            next_subscription_id: 1,
        }
    }

    fn drain_events(&mut self) -> Result<Vec<MarketObservation>, String> {
        let mut error = None;
        loop {
            match self.events.try_recv() {
                Ok(Event::Status(status)) => self.status = status,
                Ok(Event::Batch {
                    observations: values,
                    orderbooks,
                }) => {
                    self.observations.extend(values);
                    self.orderbooks.extend(orderbooks);
                }
                Ok(Event::Error(value)) => {
                    self.status = FeedStatus::Degraded;
                    error = Some(value);
                }
                Err(TryRecvError::Empty | TryRecvError::Disconnected) => break,
            }
        }
        if let Some(error) = error {
            return Err(error);
        }

        // Keep the provider worker responsive under a burst. Remaining values
        // stay owned by this worker and are returned on later polls instead of
        // monopolizing the Market actor's turn.
        let count = self.observations.len().min(MAX_EVENTS_PER_POLL);
        Ok(self.observations.drain(..count).collect())
    }
}

const MAX_EVENTS_PER_POLL: usize = 1_024;

impl MarketFeed for MarketFeedWorker {
    fn subscribe(&mut self, market: &MarketDescriptor) -> Result<SubscriptionId, String> {
        market.validate()?;
        let id = SubscriptionId::new(format!("worker:{}", self.next_subscription_id))?;
        self.next_subscription_id += 1;
        let (result_sender, result_receiver) = mpsc::channel();
        self.commands
            .try_send(Command::Subscribe {
                id: id.clone(),
                market: market.clone(),
                result: result_sender,
            })
            .map_err(command_send_error)?;
        result_receiver
            .recv_timeout(Duration::from_secs(10))
            .map_err(|error| format!("market feed worker did not acknowledge subscribe: {error}"))?
    }

    fn unsubscribe(&mut self, subscription: &SubscriptionId) -> Result<(), String> {
        self.commands
            .try_send(Command::Unsubscribe(subscription.clone()))
            .map_err(command_send_error)
    }

    fn poll(&mut self) -> Result<Vec<MarketObservation>, String> {
        self.drain_events()
    }

    fn poll_orderbooks(&mut self) -> Result<Vec<MarketOrderBookUpdate>, String> {
        let count = self.orderbooks.len().min(MAX_EVENTS_PER_POLL);
        Ok(self.orderbooks.drain(..count).collect())
    }

    fn status(&self) -> FeedStatus {
        self.status
    }

    fn recover(&mut self) -> Result<(), String> {
        self.status = FeedStatus::Reconnecting;
        self.commands
            .try_send(Command::Recover)
            .map_err(command_send_error)
    }

    fn resync_orderbook(&mut self, market_id: &str) -> Result<(), String> {
        self.commands
            .try_send(Command::ResyncOrderBook(market_id.to_owned()))
            .map_err(command_send_error)
    }
}

fn command_send_error(error: TrySendError<Command>) -> String {
    match error {
        TrySendError::Full(_) => "market feed worker command queue is full".into(),
        TrySendError::Disconnected(_) => "market feed worker is not running".into(),
    }
}

fn run_worker(
    feed: &mut dyn MarketFeed,
    commands: Receiver<Command>,
    events: SyncSender<Event>,
    poll_interval: Duration,
) {
    if let Err(error) = feed.start() {
        let _ = events.try_send(Event::Error(error));
        return;
    } else {
        let _ = events.try_send(Event::Status(feed.status()));
    }

    let mut provider_subscriptions = BTreeMap::new();
    let mut next_poll = Instant::now();
    loop {
        while let Ok(command) = commands.try_recv() {
            if !process_command(feed, &events, &mut provider_subscriptions, command) {
                return;
            }
        }

        if !provider_subscriptions.is_empty() && Instant::now() >= next_poll {
            match feed.poll().and_then(|observations| {
                feed.poll_orderbooks()
                    .map(|orderbooks| (observations, orderbooks))
            }) {
                Ok((observations, orderbooks)) => {
                    if !observations.is_empty() || !orderbooks.is_empty() {
                        if events
                            .try_send(Event::Batch {
                                observations,
                                orderbooks,
                            })
                            .is_err()
                        {
                            return;
                        }
                    }
                    if events.try_send(Event::Status(feed.status())).is_err() {
                        return;
                    }
                }
                Err(error) => {
                    if events.try_send(Event::Error(error)).is_err() {
                        return;
                    }
                }
            }
            next_poll = Instant::now() + poll_interval;
        }

        let wait = next_poll
            .saturating_duration_since(Instant::now())
            .min(Duration::from_millis(20));
        match commands.recv_timeout(wait) {
            Ok(command) => {
                if !process_command(feed, &events, &mut provider_subscriptions, command) {
                    return;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
}

fn process_command(
    feed: &mut dyn MarketFeed,
    events: &SyncSender<Event>,
    provider_subscriptions: &mut BTreeMap<SubscriptionId, SubscriptionId>,
    command: Command,
) -> bool {
    let send = |event| events.try_send(event).is_ok();
    match command {
        Command::Subscribe { id, market, result } => match feed.subscribe(&market) {
            Ok(provider_id) => {
                provider_subscriptions.insert(id.clone(), provider_id);
                if !send(Event::Status(feed.status())) {
                    return false;
                }
                // Return the same logical worker id that was submitted. The
                // connection manager stores this value and later passes it to
                // unsubscribe; returning a synthetic ack breaks that mapping.
                let _ = result.send(Ok(id));
            }
            Err(error) => {
                let _ = result.send(Err(error.clone()));
                return send(Event::Error(error));
            }
        },
        Command::Unsubscribe(id) => {
            if let Some(provider_id) = provider_subscriptions.remove(&id) {
                if let Err(error) = feed.unsubscribe(&provider_id) {
                    if !send(Event::Error(error)) {
                        return false;
                    }
                }
            }
        }
        Command::ResyncOrderBook(market_id) => {
            if let Err(error) = feed.resync_orderbook(&market_id) {
                if !send(Event::Error(error)) {
                    return false;
                }
            } else {
                if !send(Event::Status(feed.status())) {
                    return false;
                }
            }
        }
        Command::Recover => match feed.recover() {
            Ok(()) => {
                if !send(Event::Status(feed.status())) {
                    return false;
                }
            }
            Err(error) => {
                if !send(Event::Error(error)) {
                    return false;
                }
            }
        },
    }
    true
}
