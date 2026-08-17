//! Source attachment, input handling, recovery and lifecycle use cases.

use crate::domain::market::ResolvedMarket;
use crate::domain::source::{
    SourceDescriptor, SourceEpoch, SourceId, SourceRouteKey, SourceStatus,
};
use crate::services::actor::{
    AttachedSource, BusinessSubscriptionKey, MarketActor, PendingSourceRequest,
};
use crate::services::source::messages::{SourceCommand, SourceInput, SourceRequestId};
use crate::services::source::SourceActivator;
use crate::services::source::SourceHandle;
use std::collections::{BTreeMap, BTreeSet};

use super::super::{MarketApplication, MarketError};

const SOURCE_INPUT_CAPACITY: usize = 4_096;

impl MarketApplication {
    pub fn new(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
    ) -> Result<Self, MarketError> {
        Self::new_with_source_capacity(actor_id, max_dynamic_members, SOURCE_INPUT_CAPACITY)
    }

    pub(crate) fn new_with_source_capacity(
        actor_id: impl Into<String>,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, MarketError> {
        if source_input_capacity == 0 {
            return Err(MarketError::Invalid(
                "market source input capacity must be positive".into(),
            ));
        }
        let actor = MarketActor::new(actor_id, max_dynamic_members, source_input_capacity)
            .map_err(MarketError::Invalid)?;
        Ok(Self { actor })
    }

    pub(crate) fn restore_with_source_capacity(
        checkpoint: crate::services::actor::ReplayCheckpoint,
        max_dynamic_members: usize,
        source_input_capacity: usize,
    ) -> Result<Self, MarketError> {
        if source_input_capacity == 0 {
            return Err(MarketError::Invalid(
                "market source input capacity must be positive".into(),
            ));
        }
        Ok(Self {
            actor: MarketActor::restore(checkpoint, max_dynamic_members, source_input_capacity)
                .map_err(MarketError::Invalid)?,
        })
    }

    pub(crate) fn source_input_capacity(&self) -> usize {
        self.actor.source_input_capacity
    }

    pub(crate) fn attach_source(&mut self, handle: SourceHandle) -> Result<(), String> {
        self.actor.register_source(handle.descriptor.clone())?;
        let id = handle.descriptor.id.clone();
        if self.actor.attached_sources.contains_key(&id) {
            return Err(format!("market source already attached: {id}"));
        }
        self.actor.attached_sources.insert(
            id,
            AttachedSource {
                descriptor: handle.descriptor,
                commands: handle.commands,
                inputs: handle.inputs,
                task: Some(handle.task),
                confirmed: BTreeMap::new(),
            },
        );
        Ok(())
    }

    pub(crate) fn take_source_handle(
        &mut self,
        source_id: &SourceId,
    ) -> Result<SourceHandle, String> {
        self.actor.take_source_handle(source_id)
    }

    pub(crate) fn has_sources(&self) -> bool {
        !self.actor.attached_sources.is_empty()
    }

    /// Wait for exactly one source input. The Market engine selects this
    /// future alongside commands, Reference changes and maintenance timers,
    /// so live facts wake the Actor immediately instead of waiting for a poll.
    pub(crate) async fn next_source_input(&mut self) -> Option<SourceInput> {
        std::future::poll_fn(|context| {
            let source_ids = self
                .actor
                .attached_sources
                .keys()
                .cloned()
                .collect::<Vec<_>>();
            let source_count = source_ids.len();
            if source_count == 0 {
                return std::task::Poll::Pending;
            }
            let mut closed = 0;
            for offset in 0..source_count {
                let index = (self.actor.next_source_input_index + offset) % source_count;
                let source_id = &source_ids[index];
                let source = self
                    .actor
                    .attached_sources
                    .get_mut(source_id)
                    .expect("source id was collected from the same map");
                match std::pin::Pin::new(&mut source.inputs).poll_recv(context) {
                    std::task::Poll::Ready(Some(input)) => {
                        self.actor.next_source_input_index = (index + 1) % source_count;
                        return std::task::Poll::Ready(Some(input));
                    }
                    std::task::Poll::Ready(None) => closed += 1,
                    std::task::Poll::Pending => {}
                }
            }
            if closed == source_count {
                std::task::Poll::Ready(None)
            } else {
                std::task::Poll::Pending
            }
        })
        .await
    }

    /// Drive one wake-up for small embedded callers such as the one-shot CLI.
    /// Long-running processes select the same private receive future directly.
    pub async fn drive_next_source_input(&mut self) -> Result<usize, MarketError> {
        // Embedded callers do not run the long-lived engine loop which
        // normally reconciles desired subscriptions after every command.
        // Flush commands before awaiting provider input; otherwise a newly
        // requested subscription and its confirmation wait on each other.
        self.sync_source_subscriptions().await?;
        let input = self
            .next_source_input()
            .await
            .ok_or_else(|| MarketError::SourceUnavailable("input channel closed".into()))?;
        let count = self.apply_source_input(input).await?;
        self.sync_source_subscriptions().await?;
        Ok(count)
    }

    pub fn sources_complete(&self) -> bool {
        if !self.actor.attached_sources.is_empty() {
            return self
                .current_view()
                .sources
                .values()
                .all(|source| source.status == SourceStatus::Stopped);
        }
        false
    }

    pub async fn sync_source_subscriptions(&mut self) -> Result<(), MarketError> {
        self.reconcile_source_commands()
            .await
            .map_err(MarketError::Invalid)
    }

    pub(crate) async fn activate_sources_for_subscriptions(
        &mut self,
        activator: &mut dyn SourceActivator,
    ) -> Result<(), MarketError> {
        let markets = self
            .current_view()
            .subscriptions
            .into_iter()
            .flat_map(|subscription| subscription.members.into_values())
            .map(|market| (SourceRouteKey::from_market(&market), market))
            .collect::<BTreeMap<_, _>>()
            .into_values();
        for market in markets {
            let matched = self
                .actor
                .attached_sources
                .values()
                .any(|source| source_accepts(&source.descriptor, &market));
            if matched {
                continue;
            }
            let handle = activator
                .activate(&market, self.source_input_capacity())
                .await
                .map_err(MarketError::SourceUnavailable)?;
            self.attach_source(handle)
                .map_err(MarketError::SourceUnavailable)?;
        }
        Ok(())
    }

    pub async fn recover_sources(&mut self) -> Result<(), MarketError> {
        for source in self.actor.attached_sources.values_mut() {
            source
                .commands
                .send(SourceCommand::Reconnect)
                .await
                .map_err(|_| {
                    MarketError::SourceUnavailable(format!(
                        "market source command channel closed: {}",
                        source.descriptor.id
                    ))
                })?;
        }
        self.actor.pending_source_requests.clear();
        Ok(())
    }

    pub async fn set_replay_paused(&mut self, paused: bool) -> Result<(), MarketError> {
        let replay_id = SourceId::new("replay").expect("static replay source id");
        let source = self
            .actor
            .attached_sources
            .get_mut(&replay_id)
            .ok_or_else(|| {
                MarketError::Unsupported("Market runtime has no replay source".into())
            })?;
        source
            .commands
            .send(if paused {
                SourceCommand::Pause
            } else {
                SourceCommand::Resume
            })
            .await
            .map_err(|_| {
                MarketError::SourceUnavailable("replay source command channel closed".into())
            })
    }

    pub(crate) fn has_replay_source(&self) -> bool {
        let replay_id = SourceId::new("replay").expect("static replay source id");
        self.actor.attached_sources.contains_key(&replay_id)
    }

    async fn reconcile_source_commands(&mut self) -> Result<(), String> {
        let subscriptions = self.current_view().subscriptions;
        let mut desired =
            BTreeMap::<SourceId, BTreeMap<BusinessSubscriptionKey, ResolvedMarket>>::new();
        for subscription in subscriptions {
            for (market_id, market) in subscription.members {
                let matches = self
                    .actor
                    .attached_sources
                    .values()
                    .filter(|source| source_accepts(&source.descriptor, &market))
                    .map(|source| source.descriptor.id.clone())
                    .collect::<Vec<_>>();
                let source_id = match matches.as_slice() {
                    [source_id] => source_id.clone(),
                    [] => continue,
                    _ => {
                        return Err(format!(
                            "market {} matches multiple configured sources; select source_id",
                            market.market_id
                        ))
                    }
                };
                desired
                    .entry(source_id)
                    .or_default()
                    .insert((subscription.id.clone(), market_id), market);
            }
        }

        let pending_keys = self
            .actor
            .pending_source_requests
            .values()
            .filter_map(|pending| match pending {
                PendingSourceRequest::Subscribe { source_id, key }
                | PendingSourceRequest::Unsubscribe { source_id, key } => {
                    Some((source_id.clone(), key.clone()))
                }
                PendingSourceRequest::ResyncOrderBook { .. } => None,
            })
            .collect::<BTreeSet<_>>();
        let source_ids = self
            .actor
            .attached_sources
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        for source_id in source_ids {
            if self.actor.source_is_stopped(&source_id)
                || self.actor.source_command_closed(&source_id)
            {
                // A finite source may have completed before a final
                // reconciliation turn (notably a checkpointed replay). Do
                // not send new provider commands to its closed channel.
                continue;
            }
            let wanted = desired.remove(&source_id).unwrap_or_default();
            let stale = self.actor.attached_sources[&source_id]
                .confirmed
                .keys()
                .filter(|key| !wanted.contains_key(*key))
                .cloned()
                .collect::<Vec<_>>();
            for key in stale {
                if pending_keys.contains(&(source_id.clone(), key.clone())) {
                    continue;
                }
                let handle = self.actor.attached_sources[&source_id].confirmed[&key].clone();
                let request_id = self.next_request_id();
                self.actor.attached_sources[&source_id]
                    .commands
                    .send(SourceCommand::Unsubscribe { request_id, handle })
                    .await
                    .map_err(|_| format!("market source command channel closed: {source_id}"))?;
                self.actor.pending_source_requests.insert(
                    request_id,
                    PendingSourceRequest::Unsubscribe {
                        source_id: source_id.clone(),
                        key,
                    },
                );
            }
            for (key, market) in wanted {
                if self.actor.attached_sources[&source_id]
                    .confirmed
                    .contains_key(&key)
                    || pending_keys.contains(&(source_id.clone(), key.clone()))
                {
                    continue;
                }
                let request_id = self.next_request_id();
                self.actor.attached_sources[&source_id]
                    .commands
                    .send(SourceCommand::Subscribe {
                        request_id,
                        market: Box::new(market),
                    })
                    .await
                    .map_err(|_| format!("market source command channel closed: {source_id}"))?;
                self.actor.pending_source_requests.insert(
                    request_id,
                    PendingSourceRequest::Subscribe {
                        source_id: source_id.clone(),
                        key,
                    },
                );
            }
        }
        Ok(())
    }

    pub(crate) fn next_request_id(&mut self) -> SourceRequestId {
        let id = SourceRequestId::new(self.actor.next_source_request_id);
        self.actor.next_source_request_id = self.actor.next_source_request_id.saturating_add(1);
        id
    }

    pub(crate) async fn apply_source_input(
        &mut self,
        input: SourceInput,
    ) -> Result<usize, MarketError> {
        match input {
            SourceInput::StatusChanged {
                source_id,
                epoch,
                status,
                error,
            } => {
                let previous_epoch = self
                    .current_view()
                    .sources
                    .get(&source_id)
                    .map(|source| source.epoch)
                    .unwrap_or_default();
                if epoch > previous_epoch {
                    // Integration reconnect restores stable logical
                    // subscriptions. Only in-flight requests belong to the
                    // old provider session and must become eligible for
                    // reconciliation again.
                    self.actor
                        .pending_source_requests
                        .retain(|_, pending| pending.source_id() != &source_id);
                }
                self.actor
                    .apply_source_status(&source_id, epoch, status, error)
                    .map_err(MarketError::Invalid)?;
            }
            SourceInput::SubscriptionConfirmed {
                source_id,
                epoch,
                request_id,
                handle,
            } => {
                if !self.accepts_epoch(&source_id, epoch) {
                    return Ok(0);
                }
                let Some(PendingSourceRequest::Subscribe {
                    source_id: pending_source,
                    key,
                }) = self.actor.pending_source_requests.remove(&request_id)
                else {
                    return Ok(0);
                };
                if source_id == pending_source {
                    if let Some(source) = self.actor.attached_sources.get_mut(&source_id) {
                        source.confirmed.insert(key, handle);
                    }
                }
            }
            SourceInput::SubscriptionRejected {
                source_id,
                epoch,
                request_id,
                error,
            } => {
                self.actor.pending_source_requests.remove(&request_id);
                self.actor
                    .apply_source_status(&source_id, epoch, SourceStatus::Degraded, Some(error))
                    .map_err(MarketError::Invalid)?;
            }
            SourceInput::Unsubscribed {
                source_id,
                epoch,
                request_id,
            } => {
                if !self.accepts_epoch(&source_id, epoch) {
                    return Ok(0);
                }
                let Some(PendingSourceRequest::Unsubscribe {
                    source_id: pending_source,
                    key,
                }) = self.actor.pending_source_requests.remove(&request_id)
                else {
                    return Ok(0);
                };
                if source_id == pending_source {
                    if let Some(source) = self.actor.attached_sources.get_mut(&source_id) {
                        source.confirmed.remove(&key);
                    }
                }
            }
            SourceInput::Observation {
                source_id,
                epoch,
                observation,
            } => {
                if self.accepts_epoch(&source_id, epoch) {
                    self.ingest(observation)?;
                    return Ok(1);
                }
            }
            SourceInput::ReplayObservation {
                source_id,
                epoch,
                observation,
                accepted,
            } => {
                if !self.accepts_epoch(&source_id, epoch) {
                    let _ = accepted.send(Err("stale replay source epoch".into()));
                    return Ok(0);
                }
                match self.ingest(observation) {
                    Ok(_) => {
                        let _ = accepted.send(Ok(self.checkpoint()));
                        return Ok(1);
                    }
                    Err(error) => {
                        let _ = accepted.send(Err(error.to_string()));
                        return Err(error);
                    }
                }
            }
            SourceInput::OrderBook {
                source_id,
                epoch,
                update,
            } => {
                if self.accepts_epoch(&source_id, epoch) {
                    let market = (*update.market).clone();
                    match self.apply_source_orderbook(update) {
                        Ok(()) => return Ok(1),
                        Err(MarketError::Invalid(reason)) => {
                            self.request_orderbook_resync(source_id, epoch, market, reason)
                                .await?;
                        }
                        Err(error) => return Err(error),
                    }
                }
            }
            SourceInput::ResyncRequired {
                source_id,
                epoch,
                market,
                reason,
            } => {
                if self.accepts_epoch(&source_id, epoch) {
                    self.request_orderbook_resync(source_id, epoch, *market, reason)
                        .await?;
                }
            }
            SourceInput::ResyncCompleted {
                source_id,
                epoch,
                request_id,
                market_id,
            } => {
                if !self.accepts_epoch(&source_id, epoch) {
                    return Ok(0);
                }
                let Some(PendingSourceRequest::ResyncOrderBook {
                    source_id: pending_source,
                    market_id: pending_market,
                }) = self.actor.pending_source_requests.remove(&request_id)
                else {
                    return Ok(0);
                };
                if source_id == pending_source && market_id == pending_market {
                    self.actor
                        .complete_orderbook_resync(&source_id, epoch, &market_id)
                        .map_err(MarketError::Invalid)?;
                }
            }
            SourceInput::ResyncRejected {
                source_id,
                epoch,
                request_id,
                market_id,
                error,
            } => {
                self.actor.pending_source_requests.remove(&request_id);
                self.actor
                    .begin_orderbook_resync(
                        &source_id,
                        epoch,
                        &market_id,
                        format!("order-book resync rejected: {error}"),
                    )
                    .map_err(MarketError::Invalid)?;
                self.actor
                    .apply_source_status(&source_id, epoch, SourceStatus::Degraded, Some(error))
                    .map_err(MarketError::Invalid)?;
            }
            SourceInput::Failed {
                source_id,
                epoch,
                kind,
                error: reason,
            } => {
                self.actor
                    .apply_source_failure(&source_id, epoch, kind, reason)
                    .map_err(MarketError::Invalid)?;
            }
            SourceInput::Completed { source_id, epoch } => {
                self.actor
                    .apply_source_status(&source_id, epoch, SourceStatus::Stopped, None)
                    .map_err(MarketError::Invalid)?;
            }
        }
        Ok(0)
    }

    fn accepts_epoch(&self, source_id: &SourceId, epoch: SourceEpoch) -> bool {
        self.current_view()
            .sources
            .get(source_id)
            .is_some_and(|state| state.epoch == epoch)
    }

    pub(crate) async fn shutdown_sources(
        &mut self,
        timeout: std::time::Duration,
    ) -> Result<(), String> {
        let mut pending = self
            .actor
            .attached_sources
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>();
        let result = tokio::time::timeout(timeout, async {
            for (source_id, source) in &self.actor.attached_sources {
                if source.commands.send(SourceCommand::Shutdown).await.is_err() {
                    tracing::debug!(source_id = %source_id, "market source command channel was already closed during shutdown");
                }
            }
            let mut joins = tokio::task::JoinSet::new();
            for (source_id, source) in &mut self.actor.attached_sources {
                if let Some(task) = source.task.take() {
                    let source_id = source_id.clone();
                    joins.spawn(async move { (source_id, task.await) });
                }
            }
            while !joins.is_empty() {
                tokio::select! {
                    input = self.next_source_input() => {
                        if let Some(input) = input {
                            self.apply_source_input(input)
                                .await
                                .map_err(|error| error.to_string())?;
                        }
                    }
                    joined = joins.join_next() => {
                        let Some(joined) = joined else { continue };
                        let (source_id, result) = joined
                            .map_err(|error| format!("market source join wrapper failed: {error}"))?;
                        result.map_err(|error| {
                            format!("market source task failed ({source_id}): {error}")
                        })?;
                        pending.remove(&source_id);
                    }
                }
            }
            while let Some(input) = self.next_source_input().await {
                self.apply_source_input(input)
                    .await
                    .map_err(|error| error.to_string())?;
            }
            Ok::<(), String>(())
        })
        .await;
        match result {
            Ok(result) => result,
            Err(_) => Err(format!(
                "market source shutdown timed out after {}ms; pending sources: {}",
                timeout.as_millis(),
                pending
                    .iter()
                    .map(ToString::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            )),
        }
    }

    pub(crate) fn evaluate_freshness(&mut self, now_unix_nanos: u64, max_age_nanos: u64) {
        self.actor.evaluate_freshness(now_unix_nanos, max_age_nanos);
    }
}

pub(crate) fn source_accepts(source: &SourceDescriptor, market: &ResolvedMarket) -> bool {
    let source_id_matches = source.exchange_id.is_none()
        || market
            .source_id
            .as_ref()
            .is_none_or(|id| id.as_str().eq_ignore_ascii_case(source.id.as_str()));
    source_id_matches
        && source.exchange_id.as_ref().is_none_or(|exchange| {
            exchange
                .as_str()
                .strip_prefix("exchange:")
                .unwrap_or(exchange.as_str())
                .eq_ignore_ascii_case(
                    market
                        .exchange_id
                        .as_str()
                        .strip_prefix("exchange:")
                        .unwrap_or(market.exchange_id.as_str()),
                )
        })
        && source
            .market_type
            .as_ref()
            .is_none_or(|market_type| market_type == &market.route.provider_product)
        && source.asset_type.as_ref().is_none_or(|asset_type| {
            market
                .asset_type
                .as_ref()
                .is_none_or(|market_asset| market_asset == asset_type)
        })
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use kairos_primitives::{Exchange, InstrumentKind, UnixNanos};
    use tokio::sync::mpsc;

    use super::MarketApplication;
    use crate::domain::observation::{MarketObservation, Quote};
    use crate::domain::source::{
        MarketReadiness, SourceDescriptor, SourceEpoch, SourceId, SourceStatus,
    };
    use crate::domain::subscription::{
        SubscriptionId, SubscriptionMemberRequirement, SubscriptionStatus,
    };
    use crate::services::source::messages::{ProviderSubscriptionId, SourceCommand, SourceInput};
    use crate::services::source::{SourceActivator, SourceHandle};

    fn resolved_market_with_asset_type(
        market_id: &str,
        instrument_id: &str,
        exchange_id: &str,
        provider_product: &str,
        asset_type: &str,
        provider_symbol: &str,
    ) -> Result<crate::ResolvedMarket, String> {
        let kind = match provider_product {
            "spot" => InstrumentKind::Spot,
            "options" => InstrumentKind::Option,
            "perpetual" | "swap" => InstrumentKind::Perpetual,
            "future" | "futures" => InstrumentKind::Future,
            _ => InstrumentKind::Equity,
        };
        crate::ResolvedMarket::new(
            market_id,
            instrument_id,
            kind,
            exchange_id,
            crate::MarketDataRoute::new(
                format!("test:{market_id}"),
                exchange_id,
                provider_product,
                provider_symbol,
            )?,
        )?
        .with_asset_type(asset_type)
    }

    struct CountingActivator {
        calls: usize,
    }

    impl SourceActivator for CountingActivator {
        fn activate<'a>(
            &'a mut self,
            _market: &'a crate::ResolvedMarket,
            _source_input_capacity: usize,
        ) -> std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<SourceHandle, String>> + Send + 'a>,
        > {
            self.calls += 1;
            Box::pin(async move {
                let descriptor = SourceDescriptor::new(
                    SourceId::new("counted-route").unwrap(),
                    Exchange::new("binance").unwrap(),
                    "spot",
                    Some("crypto".into()),
                )
                .unwrap();
                let (commands, mut command_receiver) = mpsc::channel(2);
                let (_input_sender, inputs) = mpsc::channel(2);
                let task = tokio::spawn(async move {
                    while let Some(command) = command_receiver.recv().await {
                        if matches!(command, SourceCommand::Shutdown) {
                            return;
                        }
                    }
                });
                Ok(SourceHandle {
                    descriptor,
                    commands,
                    inputs,
                    task,
                })
            })
        }
    }

    fn attach_test_source(application: &mut MarketApplication, id: &str, stop_on_shutdown: bool) {
        let descriptor = SourceDescriptor::new(
            SourceId::new(id).unwrap(),
            Exchange::new(id).unwrap(),
            "spot",
            Some("crypto".into()),
        )
        .unwrap();
        let (commands, mut command_receiver) = mpsc::channel(1);
        let (input_sender, inputs) = mpsc::channel(1);
        let task_descriptor = descriptor.clone();
        let task = tokio::spawn(async move {
            input_sender
                .send(SourceInput::StatusChanged {
                    source_id: task_descriptor.id.clone(),
                    epoch: SourceEpoch::new(1),
                    status: SourceStatus::Ready,
                    error: None,
                })
                .await
                .unwrap();
            if stop_on_shutdown {
                while let Some(command) = command_receiver.recv().await {
                    if matches!(command, SourceCommand::Shutdown) {
                        let _ = input_sender
                            .send(SourceInput::Completed {
                                source_id: task_descriptor.id.clone(),
                                epoch: SourceEpoch::new(1),
                            })
                            .await;
                        return;
                    }
                }
            } else {
                std::future::pending::<()>().await;
            }
        });
        application
            .attach_source(SourceHandle {
                descriptor,
                commands,
                inputs,
                task,
            })
            .unwrap();
    }

    #[tokio::test]
    async fn independently_bounded_source_inputs_are_polled_fairly() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 1).unwrap();
        attach_test_source(&mut application, "source-a", true);
        attach_test_source(&mut application, "source-b", true);

        let first = application.next_source_input().await.unwrap();
        let second = application.next_source_input().await.unwrap();
        let source_id = |input: SourceInput| match input {
            SourceInput::StatusChanged { source_id, .. } => source_id,
            _ => panic!("expected source status"),
        };
        assert_eq!(source_id(first).as_str(), "source-a");
        assert_eq!(source_id(second).as_str(), "source-b");
        application
            .shutdown_sources(Duration::from_secs(1))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn subscription_projection_reports_route_and_provider_readiness() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 1).unwrap();
        attach_test_source(&mut application, "binance", true);
        let status = application.next_source_input().await.unwrap();
        application.apply_source_input(status).await.unwrap();
        let market = resolved_market_with_asset_type(
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC-USDT",
            "binance",
            "spot",
            "crypto",
            "BTCUSDT",
        )
        .unwrap();
        application
            .subscribe_static(SubscriptionId::new("btc").unwrap(), "test", market)
            .unwrap();
        application.sync_source_subscriptions().await.unwrap();
        let snapshot = application.current_view();
        assert_eq!(
            snapshot.subscriptions[0].status,
            SubscriptionStatus::Pending
        );
        assert_eq!(snapshot.subscriptions[0].member_status.len(), 1);
        application
            .shutdown_sources(Duration::from_secs(1))
            .await
            .unwrap();

        let mut empty = MarketApplication::new("empty", 10).unwrap();
        let market = resolved_market_with_asset_type(
            "market:unknown:spot:BTCUSDT",
            "instrument:spot:BTC-USDT",
            "unknown",
            "spot",
            "crypto",
            "BTCUSDT",
        )
        .unwrap();
        empty
            .subscribe_static(SubscriptionId::new("unsupported").unwrap(), "test", market)
            .unwrap();
        assert_eq!(
            empty.current_view().subscriptions[0].status,
            SubscriptionStatus::Unavailable
        );
        empty
            .set_subscription_member_requirement(
                &SubscriptionId::new("unsupported").unwrap(),
                "market:unknown:spot:BTCUSDT",
                SubscriptionMemberRequirement::Optional,
            )
            .unwrap();
        assert_eq!(
            empty.current_view().subscriptions[0].status,
            SubscriptionStatus::Degraded
        );
    }

    #[tokio::test]
    async fn concurrent_same_route_intents_share_one_activation() {
        let mut application = MarketApplication::new("market", 10).unwrap();
        let market = resolved_market_with_asset_type(
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC-USDT",
            "binance",
            "spot",
            "crypto",
            "BTCUSDT",
        )
        .unwrap();
        application
            .subscribe_static(SubscriptionId::new("one").unwrap(), "test", market.clone())
            .unwrap();
        application
            .subscribe_static(SubscriptionId::new("two").unwrap(), "test", market)
            .unwrap();
        let mut activator = CountingActivator { calls: 0 };
        application
            .activate_sources_for_subscriptions(&mut activator)
            .await
            .unwrap();
        assert_eq!(activator.calls, 1);
        assert_eq!(application.current_view().sources.len(), 1);
        application
            .shutdown_sources(Duration::from_secs(1))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn shutdown_timeout_names_unfinished_source() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 1).unwrap();
        attach_test_source(&mut application, "stuck-source", false);
        let _ = application.next_source_input().await;
        let error = application
            .shutdown_sources(Duration::from_millis(10))
            .await
            .unwrap_err();
        assert!(error.contains("stuck-source"), "{error}");
    }

    #[tokio::test]
    async fn orderbook_gap_requests_only_the_affected_market_resync() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 2).unwrap();
        let descriptor = SourceDescriptor::new(
            SourceId::new("binance-spot").unwrap(),
            Exchange::new("binance").unwrap(),
            "spot",
            Some("crypto".into()),
        )
        .unwrap();
        let (commands, mut command_receiver) = mpsc::channel(2);
        let (_input_sender, inputs) = mpsc::channel(2);
        let task = tokio::spawn(std::future::pending::<()>());
        application
            .attach_source(SourceHandle {
                descriptor,
                commands,
                inputs,
                task,
            })
            .unwrap();
        application
            .actor
            .apply_source_status(
                &SourceId::new("binance-spot").unwrap(),
                SourceEpoch::new(1),
                SourceStatus::Ready,
                None,
            )
            .unwrap();
        let market = resolved_market_with_asset_type(
            "btc-usdt", "btc", "binance", "spot", "crypto", "BTCUSDT",
        )
        .unwrap();
        application
            .ingest_orderbook_snapshot(
                crate::domain::observation::order_book::OrderBook::snapshot_with_source(
                    "binance-spot",
                    "btc-usdt",
                    "btc",
                    10,
                    1,
                    vec![],
                    vec![],
                )
                .unwrap(),
            )
            .unwrap();
        application
            .apply_source_input(SourceInput::OrderBook {
                source_id: SourceId::new("binance-spot").unwrap(),
                epoch: SourceEpoch::new(1),
                update: crate::services::source::messages::SourceOrderBookUpdate {
                    market: Box::new(market.clone()),
                    source_id: "binance-spot".into(),
                    market_id: market.market_id.clone(),
                    instrument_id: market.instrument_id.clone(),
                    first_sequence: 12.into(),
                    last_sequence: 12.into(),
                    event_time_unix_nanos: 2.into(),
                    bids: vec![],
                    asks: vec![],
                    snapshot: false,
                },
            })
            .await
            .unwrap();
        match command_receiver.recv().await.unwrap() {
            SourceCommand::ResyncOrderBook {
                market: requested, ..
            } => assert_eq!(requested.market_id, market.market_id),
            _ => panic!("expected order-book resync command"),
        }
        let snapshot = application.current_view();
        assert_eq!(
            snapshot.sources[&SourceId::new("binance-spot").unwrap()].resyncing_markets,
            vec![market.market_id]
        );
    }

    #[tokio::test]
    async fn any_active_source_failure_changes_source_readiness() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 2).unwrap();
        let optional_id = SourceId::new("optional-route").unwrap();
        let required_id = SourceId::new("required-route").unwrap();
        application
            .actor
            .register_source(
                SourceDescriptor::new(
                    optional_id.clone(),
                    Exchange::new("optional").unwrap(),
                    "spot",
                    Some("crypto".into()),
                )
                .unwrap(),
            )
            .unwrap();
        application
            .actor
            .register_source(
                SourceDescriptor::new(
                    required_id.clone(),
                    Exchange::new("required").unwrap(),
                    "spot",
                    Some("crypto".into()),
                )
                .unwrap(),
            )
            .unwrap();
        for source_id in [&optional_id, &required_id] {
            application
                .apply_source_input(SourceInput::StatusChanged {
                    source_id: source_id.clone(),
                    epoch: SourceEpoch::new(1),
                    status: SourceStatus::Ready,
                    error: None,
                })
                .await
                .unwrap();
        }
        application
            .apply_source_input(SourceInput::Failed {
                source_id: optional_id.clone(),
                epoch: SourceEpoch::new(1),
                kind: crate::domain::source::SourceFailureKind::Transport,
                error: "controlled disconnect".into(),
            })
            .await
            .unwrap();

        let snapshot = application.current_view();
        assert_eq!(snapshot.readiness, MarketReadiness::Degraded);
        assert_eq!(
            snapshot.sources[&optional_id].status,
            SourceStatus::Degraded
        );
        assert_eq!(
            snapshot.sources[&optional_id].last_failure_kind,
            Some(crate::domain::source::SourceFailureKind::Transport)
        );
        assert_eq!(snapshot.sources[&required_id].status, SourceStatus::Ready);
        assert_eq!(snapshot.sources[&required_id].epoch, SourceEpoch::new(1));
    }

    #[tokio::test]
    async fn stale_route_epoch_observation_cannot_mutate_actor_state() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 2).unwrap();
        let source_id = SourceId::new("binance-spot").unwrap();
        application
            .actor
            .register_source(
                SourceDescriptor::new(
                    source_id.clone(),
                    Exchange::new("binance").unwrap(),
                    "spot",
                    Some("crypto".into()),
                )
                .unwrap(),
            )
            .unwrap();
        application
            .apply_source_input(SourceInput::StatusChanged {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(2),
                status: SourceStatus::Ready,
                error: None,
            })
            .await
            .unwrap();
        let market = resolved_market_with_asset_type(
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC-USDT",
            "binance",
            "spot",
            "crypto",
            "BTCUSDT",
        )
        .unwrap();
        application
            .subscribe_static(SubscriptionId::new("btc").unwrap(), "test", market.clone())
            .unwrap();
        let observation = MarketObservation::Quote(Quote {
            market_id: market.market_id,
            instrument_id: market.instrument_id,
            bid_price: Some("100".parse().unwrap()),
            bid_quantity: Some("1".parse().unwrap()),
            ask_price: Some("101".parse().unwrap()),
            ask_quantity: Some("1".parse().unwrap()),
            observed_at_unix_nanos: UnixNanos::new(1),
            source_id: source_id.to_string(),
        });
        application
            .apply_source_input(SourceInput::Observation {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(1),
                observation: observation.clone(),
            })
            .await
            .unwrap();
        assert_eq!(application.event_sequence(), 0);
        application
            .apply_source_input(SourceInput::Observation {
                source_id,
                epoch: SourceEpoch::new(2),
                observation,
            })
            .await
            .unwrap();
        assert_eq!(application.event_sequence(), 1);
    }

    #[tokio::test]
    async fn epoch_advance_retries_pending_subscription_and_rejects_stale_ack() {
        let mut application = MarketApplication::new_with_source_capacity("market", 10, 4).unwrap();
        let source_id = SourceId::new("binance-spot").unwrap();
        let descriptor = SourceDescriptor::new(
            source_id.clone(),
            Exchange::new("binance").unwrap(),
            "spot",
            Some("crypto".into()),
        )
        .unwrap();
        let (commands, mut command_receiver) = mpsc::channel(4);
        let (_input_sender, inputs) = mpsc::channel(4);
        application
            .attach_source(SourceHandle {
                descriptor,
                commands,
                inputs,
                task: tokio::spawn(std::future::pending::<()>()),
            })
            .unwrap();
        application
            .apply_source_input(SourceInput::StatusChanged {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(1),
                status: SourceStatus::Ready,
                error: None,
            })
            .await
            .unwrap();
        let market = resolved_market_with_asset_type(
            "market:binance:spot:BTCUSDT",
            "instrument:spot:BTC-USDT",
            "binance",
            "spot",
            "crypto",
            "BTCUSDT",
        )
        .unwrap();
        application
            .subscribe_static(SubscriptionId::new("btc").unwrap(), "test", market)
            .unwrap();
        application.sync_source_subscriptions().await.unwrap();
        let SourceCommand::Subscribe {
            request_id: stale_request,
            ..
        } = command_receiver.recv().await.unwrap()
        else {
            panic!("expected initial subscribe")
        };

        application
            .apply_source_input(SourceInput::StatusChanged {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(2),
                status: SourceStatus::Ready,
                error: None,
            })
            .await
            .unwrap();
        application.sync_source_subscriptions().await.unwrap();
        let SourceCommand::Subscribe {
            request_id: current_request,
            ..
        } = command_receiver.recv().await.unwrap()
        else {
            panic!("expected retried subscribe")
        };
        application
            .apply_source_input(SourceInput::SubscriptionConfirmed {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(1),
                request_id: stale_request,
                handle: ProviderSubscriptionId::new("stale").unwrap(),
            })
            .await
            .unwrap();
        application
            .apply_source_input(SourceInput::SubscriptionConfirmed {
                source_id: source_id.clone(),
                epoch: SourceEpoch::new(2),
                request_id: current_request,
                handle: ProviderSubscriptionId::new("current").unwrap(),
            })
            .await
            .unwrap();

        let confirmed = &application.actor.attached_sources[&source_id].confirmed;
        assert_eq!(confirmed.len(), 1);
        assert_eq!(confirmed.values().next().unwrap().as_str(), "current");
    }
}
