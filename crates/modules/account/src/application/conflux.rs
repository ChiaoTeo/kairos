use std::collections::BTreeMap;
use std::convert::Infallible;
use std::time::{Duration, Instant};

use kairos_conflux::{
    AccountQuery, ConfluxActor, ConfluxEvent, Context, Contract, EarnPositionsRequest,
    EarnProductFamily, EarnProductQuery, ExternalAccountEvent, ExternalAccountEventEnvelope,
    ExternalParticipantEvent, ResourceOperationError, RestContract, SystemEvent,
    TypedConnectionCollection,
};
use kairos_primitives::SegmentKey;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

use super::{
    AccountApplication, AccountError, AccountFactProvenance, AccountSegmentCompleteness,
    AccountSegmentFreshness, AccountSegmentSyncLifecycle, AccountSegmentSyncMode, RefreshAccount,
};
use crate::domain::{AccountEvent, AccountFill};
use crate::services::integration::{
    AccountInstrumentResolver, external_segment, map_earn_positions, map_event, map_snapshot,
};
use crate::services::publication::{
    encode_account_current_view, encode_business_change, encode_observed_orders_current_view,
    now_unix_nanos,
};
use crate::services::refresh::RefreshFetch;
use crate::services::synchronization::{RETAINED_EVENT_IDS, SegmentSyncState};

pub struct AccountRest;

impl RestContract for AccountRest {
    type Request = kairos_account_contract::AccountRestRequest;
    type Response = kairos_account_contract::AccountRestResponse;
}

impl Contract for AccountApplication {
    type Rest = AccountRest;
}

pub(super) struct AccountConfluxState {
    refresh_interval: Duration,
    resolver: AccountInstrumentResolver,
    segments: BTreeMap<SegmentKey, SegmentSyncState>,
    identity: InstanceIdentity,
    producer_incarnation: u64,
    published_generation: Option<u64>,
}

impl Default for AccountConfluxState {
    fn default() -> Self {
        Self {
            refresh_interval: Duration::from_secs(30),
            resolver: AccountInstrumentResolver::default(),
            segments: BTreeMap::new(),
            identity: InstanceIdentity::default(),
            producer_incarnation: kairos_workspace::ProducerIncarnation::allocate().get(),
            published_generation: None,
        }
    }
}

impl AccountApplication {
    pub(crate) fn configure_conflux(
        &mut self,
        refresh_interval: Duration,
        resolver: AccountInstrumentResolver,
    ) -> Result<(), String> {
        if refresh_interval.is_zero() {
            return Err("Account refresh interval must be positive".into());
        }
        self.conflux.refresh_interval = refresh_interval;
        self.conflux.resolver = resolver;
        self.conflux.segments = self
            .current_view_shared()
            .segments
            .iter()
            .map(|segment| {
                (
                    segment.segment_key.clone(),
                    SegmentSyncState::new(segment.segment_key.clone()),
                )
            })
            .collect();
        Ok(())
    }

    pub fn configure_publication_identity(&mut self, identity: InstanceIdentity) {
        self.conflux.identity = identity;
    }
}

impl ConfluxActor for AccountApplication {
    type FatalError = AccountError;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.register_streams(context)?;
        self.refresh_from_system(context, Vec::new()).await?;
        self.publish(context)?;
        context.spawn_timer("refresh", self.conflux.refresh_interval);
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent<Self, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<kairos_account_contract::AccountRestResponse>, Self::FatalError> {
        let response = match event {
            ConfluxEvent::Rest(request) => self.handle_rest(request, context).await.map(Some),
            ConfluxEvent::Integration(event) => {
                if let ExternalParticipantEvent::Account(envelope) = event.event {
                    self.handle_account_event(
                        event.identity.descriptor.connection_key.as_str(),
                        envelope,
                    )?;
                }
                Ok(None)
            },
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "refresh" => {
                self.evaluate_freshness();
                self.refresh_from_system(context, Vec::new()).await?;
                Ok(None)
            },
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                if let Some(binding) = source.strip_prefix("integration:") {
                    if let Ok(segment) = SegmentKey::new(binding) {
                        if let Some(state) = self.conflux.segments.get_mut(&segment) {
                            state.mark_stream_ready(binding);
                        }
                    }
                }
                Ok(None)
            },
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                if let Some(binding) = source.strip_prefix("integration:") {
                    if let Ok(segment) = SegmentKey::new(binding) {
                        if let Some(state) = self.conflux.segments.get_mut(&segment) {
                            state.mark_stream_failed(binding, error);
                        }
                    }
                }
                Ok(None)
            },
            ConfluxEvent::Local(value) => match value {},
            _ => Ok(None),
        }?;
        self.publish(context)?;
        Ok(response)
    }

    async fn stopping(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        for state in self.conflux.segments.values_mut() {
            state.lifecycle = crate::services::synchronization::SegmentSyncLifecycle::Stopped;
        }
        self.conflux.published_generation = None;
        self.publish(context)
    }
}

impl AccountApplication {
    fn register_streams(&mut self, context: &mut Context<'_, Self>) -> Result<(), AccountError> {
        macro_rules! register {
            ($connections:expr) => {{
                let keys = $connections
                    .keys()
                    .into_iter()
                    .map(|key| key.to_string())
                    .collect::<Vec<_>>();
                for key in keys {
                    let segment = SegmentKey::new(key.clone())
                        .map_err(|error| AccountError::Invalid(error.to_string()))?;
                    self.conflux
                        .segments
                        .get_mut(&segment)
                        .ok_or_else(|| {
                            AccountError::Invalid(format!(
                                "Account stream references unknown segment {segment}"
                            ))
                        })?
                        .add_stream(key.clone());
                }
            }};
        }
        let connections = context.connections();
        register!(connections.binance_spot_user_websocket);
        register!(connections.binance_margin_user_websocket);
        register!(connections.binance_usdm_user_websocket);
        register!(connections.binance_coinm_user_websocket);
        register!(connections.binance_options_user_websocket);
        register!(connections.okx_private_websocket);
        register!(connections.ibkr_account_stream);
        Ok(())
    }

    async fn refresh_from_system(
        &mut self,
        context: &mut Context<'_, Self>,
        selected: Vec<SegmentKey>,
    ) -> Result<(), AccountError> {
        let view = self.current_view_shared();
        let account_id = view
            .segments
            .first()
            .map(|segment| segment.account_id.clone())
            .ok_or_else(|| AccountError::Invalid("Account has no configured segment".into()))?;
        let request = RefreshAccount {
            account_id: account_id.clone(),
            segments: selected,
        };
        let segments = self.selected_refresh_segments(&request)?;
        for segment in &segments {
            if let Some(state) = self.conflux.segments.get_mut(&segment.segment_key) {
                state.begin_refresh();
            }
        }
        if self.has_refresh_worker() {
            let report = self.refresh_report(request)?;
            let view = self.current_view_shared();
            for segment in &segments {
                let state = self
                    .conflux
                    .segments
                    .get_mut(&segment.segment_key)
                    .expect("selected segment has synchronization state");
                if let Some(issue) = report
                    .issues
                    .iter()
                    .find(|issue| issue.segment_key == segment.segment_key)
                {
                    state.snapshot_failed(issue.error.clone(), issue.elapsed_ms);
                } else if report.refreshed_segments.contains(&segment.segment_key) {
                    let observed_at = view
                        .segments
                        .iter()
                        .find(|value| value.segment_key == segment.segment_key)
                        .map(|value| value.observed_at_unix_nanos.get())
                        .unwrap_or_default();
                    state.snapshot_succeeded(observed_at, 0);
                    state.complete_resync();
                }
            }
            return Ok(());
        }
        let by_key = segments
            .into_iter()
            .map(|segment| (segment.segment_key.to_string(), segment))
            .collect::<BTreeMap<_, _>>();
        let resolver = self.conflux.resolver.clone();
        let mut connections = context.connections();
        let mut fetches = Vec::new();
        fetches
            .extend(fetch_accounts(&mut connections.binance_spot_rest, &by_key, &resolver).await);
        fetches.extend(
            fetch_accounts(&mut connections.binance_funding_rest, &by_key, &resolver).await,
        );
        fetches
            .extend(fetch_accounts(&mut connections.binance_margin_rest, &by_key, &resolver).await);
        fetches
            .extend(fetch_accounts(&mut connections.binance_usdm_rest, &by_key, &resolver).await);
        fetches
            .extend(fetch_accounts(&mut connections.binance_coinm_rest, &by_key, &resolver).await);
        fetches.extend(
            fetch_accounts(&mut connections.binance_options_rest, &by_key, &resolver).await,
        );
        fetches.extend(fetch_accounts(&mut connections.okx_private_rest, &by_key, &resolver).await);
        fetches
            .extend(fetch_accounts(&mut connections.ibkr_account_query, &by_key, &resolver).await);
        let earn_fetches = fetch_earn_positions(&mut connections.binance_earn_rest, &by_key).await;

        let fetched = fetches
            .iter()
            .map(|fetch| fetch.segment.segment_key.to_string())
            .collect::<std::collections::BTreeSet<_>>();
        for (key, segment) in &by_key {
            if !fetched.contains(key) {
                fetches.push(RefreshFetch {
                    segment: segment.clone(),
                    result: Err(format!(
                        "Account segment {key} has no configured AccountQuery connection"
                    )),
                    elapsed_ms: 0,
                });
            }
        }
        let outcomes = fetches.clone();
        let report = self.apply_refresh_fetches(account_id.as_str(), fetches)?;
        for fetch in &outcomes {
            let state = self
                .conflux
                .segments
                .get_mut(&fetch.segment.segment_key)
                .expect("selected segment has synchronization state");
            if let Some(issue) = report
                .issues
                .iter()
                .find(|issue| issue.segment_key == fetch.segment.segment_key)
            {
                state.snapshot_failed(issue.error.clone(), issue.elapsed_ms);
            } else if let Ok(snapshot) = &fetch.result {
                state.snapshot_succeeded(snapshot.observed_at_unix_nanos.get(), fetch.elapsed_ms);
            }
        }
        for (segment, result, elapsed_ms) in earn_fetches {
            match result {
                Ok(snapshot) => {
                    self.apply_event(AccountEvent::EarnHoldings(snapshot))?;
                },
                Err(error) => {
                    if let Some(state) = self.conflux.segments.get_mut(&segment) {
                        state.snapshot_failed(
                            format!("Binance Earn refresh failed: {error}"),
                            elapsed_ms,
                        );
                    }
                },
            }
        }
        let keys = by_key.keys().cloned().collect::<Vec<_>>();
        for key in keys {
            let segment =
                SegmentKey::new(key).map_err(|error| AccountError::Invalid(error.to_string()))?;
            if !report.refreshed_segments.contains(&segment) {
                continue;
            }
            self.replay_buffered(&segment)?;
            if let Some(state) = self.conflux.segments.get_mut(&segment) {
                state.complete_resync();
            }
        }
        Ok(())
    }

    async fn handle_rest(
        &mut self,
        request: kairos_account_contract::AccountRestRequest,
        context: &mut Context<'_, Self>,
    ) -> Result<kairos_account_contract::AccountRestResponse, AccountError> {
        use kairos_account_contract::{
            AccountCommandStatus, AccountRestRequest, AccountRestResponse,
        };
        Ok(match request {
            AccountRestRequest::Health => AccountRestResponse::Health(Ok(self.contract_health())),
            AccountRestRequest::ApplySimulatedSettlement(value) => {
                AccountRestResponse::ApplySimulatedSettlement(
                    simulated_fill(value)
                        .and_then(|fill| self.apply_simulated_fill(fill))
                        .map(|()| AccountCommandStatus {
                            status: "applied".into(),
                        })
                        .map_err(control_error),
                )
            },
            AccountRestRequest::MarkToMarket(value) => AccountRestResponse::MarkToMarket(
                self.mark_to_market(value.into())
                    .map(|()| AccountCommandStatus {
                        status: "applied".into(),
                    })
                    .map_err(control_error),
            ),
            AccountRestRequest::AdvanceTime(value) => AccountRestResponse::AdvanceTime(
                self.advance_business_time(value.event_time_unix_nanos.get())
                    .map(|()| kairos_account_contract::AdvanceAccountTimeResponse {
                        event_time_unix_nanos: value.event_time_unix_nanos,
                    })
                    .map_err(control_error),
            ),
            AccountRestRequest::Refresh(value) => {
                let segments = value.segments;
                let result = self
                    .refresh_from_system(context, segments.clone())
                    .await
                    .map(|()| self.refresh_response(segments));
                AccountRestResponse::Refresh(result.map_err(control_error))
            },
            AccountRestRequest::Reconcile(value) => {
                let segments = value.segments;
                let result = self
                    .refresh_from_system(context, segments.clone())
                    .await
                    .map(|()| self.refresh_response(segments));
                AccountRestResponse::Reconcile(result.map_err(control_error))
            },
        })
    }

    fn refresh_response(
        &self,
        segments: Vec<SegmentKey>,
    ) -> kairos_account_contract::AccountRefreshResponse {
        let account_id = self
            .current_view_shared()
            .segments
            .first()
            .map(|value| value.account_id.clone());
        kairos_account_contract::AccountRefreshResponse {
            status: "completed".into(),
            account_id,
            segments,
        }
    }

    fn contract_health(&self) -> kairos_account_contract::Health {
        let ready = !self.conflux.segments.is_empty()
            && self.conflux.segments.values().all(SegmentSyncState::ready);
        kairos_account_contract::Health {
            status: if ready { "ready" } else { "degraded" }.into(),
            lease_valid: None,
            generation: self.generation().into(),
            event_sequence: self.event_sequence().into(),
        }
    }

    fn handle_account_event(
        &mut self,
        connection: &str,
        envelope: ExternalAccountEventEnvelope,
    ) -> Result<(), AccountError> {
        let segment = SegmentKey::new(connection)
            .map_err(|error| AccountError::Invalid(error.to_string()))?;
        let state = self.conflux.segments.get_mut(&segment).ok_or_else(|| {
            AccountError::Invalid(format!(
                "Account event references unknown segment {segment}"
            ))
        })?;
        if state.requires_buffering() {
            if !state.buffer(envelope) {
                return Err(AccountError::Source(format!(
                    "Account recovery buffer overflowed for segment {segment}"
                )));
            }
            return Ok(());
        }
        match self.apply_external_envelope(&segment, envelope.clone()) {
            Ok(_) => Ok(()),
            Err(error) => {
                let state = self
                    .conflux
                    .segments
                    .get_mut(&segment)
                    .expect("validated segment");
                state.mark_resync(error.to_string());
                if !state.buffer(envelope) {
                    return Err(AccountError::Source(format!(
                        "Account recovery buffer overflowed for segment {segment}"
                    )));
                }
                Ok(())
            },
        }
    }

    fn replay_buffered(&mut self, segment: &SegmentKey) -> Result<(), AccountError> {
        loop {
            let event = self
                .conflux
                .segments
                .get_mut(segment)
                .and_then(|state| state.recovery_events.pop_front());
            let Some(event) = event else { break };
            self.apply_external_envelope(segment, event)?;
        }
        Ok(())
    }

    fn apply_external_envelope(
        &mut self,
        segment: &SegmentKey,
        envelope: ExternalAccountEventEnvelope,
    ) -> Result<usize, AccountError> {
        validate_segment(segment, &envelope.payload).map_err(AccountError::Source)?;
        let channel = (
            envelope.connection_key.to_string(),
            envelope.channel_id.clone(),
        );
        let state = self
            .conflux
            .segments
            .get(segment)
            .expect("validated segment");
        if envelope
            .participant_event_id
            .as_ref()
            .is_some_and(|event_id| {
                state.event_ids.contains(&(
                    envelope.connection_key.to_string(),
                    envelope.channel_id.clone(),
                    event_id.clone(),
                ))
            })
        {
            return Ok(0);
        }
        if let Some((epoch, sequence)) = state.event_watermarks.get(&channel).copied() {
            if envelope.channel_epoch < epoch {
                return Ok(0);
            }
            if envelope.channel_epoch == epoch {
                if let (Some(previous), Some(current)) = (sequence, envelope.participant_sequence) {
                    if current <= previous {
                        return Ok(0);
                    }
                    if current > previous.saturating_add(1) {
                        return Err(AccountError::Source(format!(
                            "Account stream sequence gap: expected {}, received {current}",
                            previous.saturating_add(1)
                        )));
                    }
                }
            }
        }
        let event =
            map_event(envelope.payload, &self.conflux.resolver).map_err(AccountError::Source)?;
        let applied = self.apply_event_with_provenance(
            event,
            AccountFactProvenance {
                source_id: format!("{}:{}", envelope.participant.id, envelope.connection_key),
                provider_event_id: envelope.participant_event_id.clone(),
                provider_sequence: envelope.participant_sequence,
                provider_occurred_at_unix_nanos: Some(envelope.observed_at_unix_nanos.get()),
                provider_received_at_unix_nanos: Some(envelope.received_at_unix_nanos.get()),
            },
        )?;
        let state = self
            .conflux
            .segments
            .get_mut(segment)
            .expect("validated segment");
        state.event_watermarks.insert(
            channel,
            (envelope.channel_epoch, envelope.participant_sequence),
        );
        if let Some(event_id) = envelope.participant_event_id {
            let key = (
                envelope.connection_key.to_string(),
                envelope.channel_id,
                event_id,
            );
            if state.event_ids.insert(key.clone()) {
                state.event_id_order.push_back(key);
                if state.event_id_order.len() > RETAINED_EVENT_IDS {
                    if let Some(expired) = state.event_id_order.pop_front() {
                        state.event_ids.remove(&expired);
                    }
                }
            }
        }
        state.mark_event_success(envelope.observed_at_unix_nanos.get());
        Ok(applied)
    }

    fn evaluate_freshness(&mut self) {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64;
        for state in self.conflux.segments.values_mut() {
            state.evaluate_freshness(
                now,
                self.conflux.refresh_interval.saturating_mul(2),
                self.conflux.refresh_interval.saturating_mul(6),
            );
        }
    }

    fn publish(&mut self, context: &mut Context<'_, Self>) -> Result<(), AccountError> {
        const CURRENT: &str = "account-current";
        const OBSERVED_ORDERS: &str = "account-observed-orders";
        const EVENTS: &str = "account-events";

        while let Some(event) = self.pending_business_event().cloned() {
            let event_key = EVENTS.to_owned();
            for (index, change) in event.changes.iter().enumerate() {
                let bytes = encode_business_change(
                    self.actor_id(),
                    &self.conflux.identity,
                    &event,
                    index,
                    change,
                )
                .map_err(AccountError::Publication)?;
                match context
                    .system()
                    .account_event_publishers
                    .try_with(&event_key, |publisher| publisher.publish(&bytes))
                {
                    Ok(()) => {},
                    Err(ResourceOperationError::NotFound) => return Ok(()),
                    Err(ResourceOperationError::Operation(error)) => {
                        return Err(AccountError::Publication(error.to_string()));
                    },
                }
            }
            self.acknowledge_business_event()?;
        }

        let mut view = (*self.current_view_shared()).clone();
        self.enrich_current_view(&mut view);
        if self.conflux.published_generation == Some(view.generation.get()) {
            return Ok(());
        }
        let metadata = SnapshotEnvelopeMetadata {
            resource_epoch: 1,
            producer_incarnation: self.conflux.producer_incarnation,
            generation: view.generation.get(),
            applied_event_sequence: view.event_sequence.get(),
            published_at_unix_nanos: now_unix_nanos(),
        };
        let current_key = CURRENT.to_owned();
        let bytes = encode_account_current_view(self.actor_id(), &self.conflux.identity, &view)
            .map_err(AccountError::Publication)?;
        match context
            .system()
            .account_view_publishers
            .try_with(&current_key, |publisher| {
                publisher.publish(metadata, &bytes)
            }) {
            Ok(()) | Err(ResourceOperationError::NotFound) => {},
            Err(ResourceOperationError::Operation(error)) => {
                return Err(AccountError::Publication(error.to_string()));
            },
        }
        let orders_key = OBSERVED_ORDERS.to_owned();
        let bytes =
            encode_observed_orders_current_view(self.actor_id(), &self.conflux.identity, &view)
                .map_err(AccountError::Publication)?;
        match context
            .system()
            .account_view_publishers
            .try_with(&orders_key, |publisher| publisher.publish(metadata, &bytes))
        {
            Ok(()) | Err(ResourceOperationError::NotFound) => {},
            Err(ResourceOperationError::Operation(error)) => {
                return Err(AccountError::Publication(error.to_string()));
            },
        }
        self.conflux.published_generation = Some(view.generation.get());
        Ok(())
    }

    fn enrich_current_view(&self, view: &mut super::AccountCurrentView) {
        use crate::services::synchronization::{SegmentSyncLifecycle, SegmentSyncMode};

        for segment in &mut view.segments {
            let Some(state) = self.conflux.segments.get(&segment.segment_key) else {
                continue;
            };
            segment.sync_mode = match state.mode {
                SegmentSyncMode::SnapshotThenStream => AccountSegmentSyncMode::SnapshotThenStream,
                SegmentSyncMode::SnapshotOnly => AccountSegmentSyncMode::SnapshotOnly,
            };
            segment.sync_lifecycle = match state.lifecycle {
                SegmentSyncLifecycle::Configured => AccountSegmentSyncLifecycle::Configured,
                SegmentSyncLifecycle::Bootstrapping => AccountSegmentSyncLifecycle::Bootstrapping,
                SegmentSyncLifecycle::Live => AccountSegmentSyncLifecycle::Live,
                SegmentSyncLifecycle::SnapshotCurrent => {
                    AccountSegmentSyncLifecycle::SnapshotCurrent
                },
                SegmentSyncLifecycle::Degraded => AccountSegmentSyncLifecycle::Degraded,
                SegmentSyncLifecycle::Resyncing => AccountSegmentSyncLifecycle::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentSyncLifecycle::Unavailable,
                SegmentSyncLifecycle::Stopped => AccountSegmentSyncLifecycle::Stopped,
            };
            segment.freshness = match state.lifecycle {
                SegmentSyncLifecycle::Live | SegmentSyncLifecycle::SnapshotCurrent => {
                    segment.freshness
                },
                SegmentSyncLifecycle::Degraded | SegmentSyncLifecycle::Stopped => {
                    AccountSegmentFreshness::Stale
                },
                SegmentSyncLifecycle::Resyncing => AccountSegmentFreshness::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentFreshness::Unavailable,
                SegmentSyncLifecycle::Configured | SegmentSyncLifecycle::Bootstrapping => {
                    AccountSegmentFreshness::Unknown
                },
            };
            segment.completeness = if state.initial_snapshot_complete {
                AccountSegmentCompleteness::Complete
            } else {
                AccountSegmentCompleteness::Unknown
            };
            segment.snapshot_watermark = state.last_snapshot_at_unix_nanos;
            segment.event_watermark = state
                .event_watermarks
                .values()
                .filter_map(|(_, sequence)| *sequence)
                .max();
            segment.channel_epoch = state
                .event_watermarks
                .values()
                .map(|(epoch, _)| *epoch)
                .max();
            segment.last_event_at_unix_nanos = state.last_event_at_unix_nanos;
            segment.last_success_at_unix_nanos = state.last_success_at_unix_nanos;
            segment.last_error.clone_from(&state.last_error);
            segment.recovery_buffer_depth = state.recovery_events.len() as u64;
        }
    }
}

async fn fetch_accounts<C: AccountQuery, P>(
    connections: &mut TypedConnectionCollection<'_, C, P>,
    segments: &BTreeMap<String, crate::domain::AccountSegment>,
    resolver: &AccountInstrumentResolver,
) -> Vec<RefreshFetch> {
    let mut fetches = Vec::new();
    for key in connections.keys() {
        let Some(segment) = segments.get(key.as_str()).cloned() else {
            continue;
        };
        let started = Instant::now();
        let result = connections
            .get(&key)
            .expect("key returned by typed connection collection")
            .fetch_account(&external_segment(&segment))
            .await
            .map_err(|error| error.to_string())
            .and_then(|snapshot| map_snapshot(snapshot, resolver));
        fetches.push(RefreshFetch {
            segment,
            result,
            elapsed_ms: started.elapsed().as_millis() as u64,
        });
    }
    fetches
}

async fn fetch_earn_positions<C: EarnProductQuery, P>(
    connections: &mut TypedConnectionCollection<'_, C, P>,
    segments: &BTreeMap<String, crate::domain::AccountSegment>,
) -> Vec<(
    SegmentKey,
    Result<crate::domain::EarnHoldingsSnapshot, String>,
    u64,
)> {
    let mut fetches = Vec::new();
    for key in connections.keys() {
        let Some(segment) = segments.get(key.as_str()) else {
            continue;
        };
        let started = Instant::now();
        let mut request = EarnPositionsRequest {
            family: Some(EarnProductFamily::Flexible),
            limit: Some(100),
            ..EarnPositionsRequest::default()
        };
        let mut positions = Vec::new();
        let result = async {
            for _ in 0..100 {
                let page = connections
                    .get(&key)
                    .expect("key returned by typed connection collection")
                    .positions(&request)
                    .await
                    .map_err(|error| error.to_string())?;
                positions.extend(page.items);
                let Some(cursor) = page.next_cursor else {
                    return Ok(map_earn_positions(
                        segment.segment_key.clone(),
                        positions,
                        now_unix_nanos().into(),
                        true,
                    ));
                };
                request.cursor = Some(cursor);
            }
            Err("Binance Earn positions exceeded the 100-page safety bound".into())
        }
        .await;
        fetches.push((
            segment.segment_key.clone(),
            result,
            started.elapsed().as_millis() as u64,
        ));
    }
    fetches
}

fn control_error(error: AccountError) -> kairos_account_contract::AccountControlError {
    kairos_account_contract::AccountControlError {
        code: "account.request_failed".into(),
        message: error.to_string(),
        retryable: matches!(
            error,
            AccountError::Source(_) | AccountError::Publication(_)
        ),
        details: BTreeMap::new(),
    }
}

fn simulated_fill(
    value: kairos_account_contract::SimulatedSettlement,
) -> Result<AccountFill, AccountError> {
    Ok(AccountFill {
        fill_id: value.fill_id,
        order_id: value.order_id,
        segment_key: value.segment_key,
        instrument_id: value.instrument_id,
        quantity: value.quantity,
        price: value.price,
        side: value.side,
        settlement_asset: value.settlement_asset,
        settlement_delta: value.settlement_delta,
        fee_asset: value.fee_asset,
        fee_amount: value.fee_amount,
        occurred_at_unix_nanos: value.occurred_at_unix_nanos,
    })
}

fn validate_segment(expected: &SegmentKey, event: &ExternalAccountEvent) -> Result<(), String> {
    match event {
        ExternalAccountEvent::Snapshot(snapshot) if &snapshot.segment_key != expected => {
            Err(format!(
                "Account stream segment mismatch: expected {expected}, received {}",
                snapshot.segment_key
            ))
        },
        ExternalAccountEvent::Fill(fill) if &fill.segment_key != expected => Err(format!(
            "Account stream segment mismatch: expected {expected}, received {}",
            fill.segment_key
        )),
        ExternalAccountEvent::Batch(events) => {
            for event in events {
                validate_segment(expected, event)?;
            }
            Ok(())
        },
        _ => Ok(()),
    }
}
