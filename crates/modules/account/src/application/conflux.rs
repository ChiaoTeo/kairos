use std::collections::BTreeMap;
use std::convert::Infallible;
use std::time::{Duration, Instant};

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, Context, Contract, ManagedConnections, RestContract, SystemEvent,
};
use kairos_integration::{
    AccountQuery, ExternalAccountEvent, ExternalAccountEventEnvelope, ExternalParticipantEvent,
};
use kairos_primitives::SegmentKey;
use kairos_protocol::InstanceIdentity;
use kairos_transport::SnapshotEnvelopeMetadata;

use super::{
    AccountApplication, AccountError, AccountFactProvenance, AccountSegmentCompleteness,
    AccountSegmentFreshness, AccountSegmentSyncLifecycle, AccountSegmentSyncMode, MarkToMarket,
    RefreshAccount,
};
use crate::domain::{
    AccountFill, FillId, InstrumentId, OrderSide, Price, Quantity, SignedQuantity,
};
use crate::services::integration::{
    external_segment, map_event, map_snapshot, AccountInstrumentResolver,
};
use crate::services::publication::{
    encode_account_current_view, encode_business_change, encode_observed_orders_current_view,
    now_unix_nanos,
};
use crate::services::refresh::RefreshFetch;
use crate::services::synchronization::{SegmentSyncState, RETAINED_EVENT_IDS};

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
        self.spawn_streams(context)?;
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
                    self.handle_account_event(&event.connection, envelope)?;
                }
                Ok(None)
            }
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "refresh" => {
                self.evaluate_freshness();
                self.refresh_from_system(context, Vec::new()).await?;
                Ok(None)
            }
            ConfluxEvent::System(SystemEvent::SourceReady { source }) => {
                if let Some(binding) = source.strip_prefix("integration:") {
                    if let Ok(segment) = SegmentKey::new(binding) {
                        if let Some(state) = self.conflux.segments.get_mut(&segment) {
                            state.mark_stream_ready(binding);
                        }
                    }
                }
                Ok(None)
            }
            ConfluxEvent::System(SystemEvent::SourceFailed { source, error }) => {
                if let Some(binding) = source.strip_prefix("integration:") {
                    if let Ok(segment) = SegmentKey::new(binding) {
                        if let Some(state) = self.conflux.segments.get_mut(&segment) {
                            state.mark_stream_failed(binding, error);
                        }
                    }
                }
                Ok(None)
            }
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
    fn spawn_streams(&mut self, context: &mut Context<'_, Self>) -> Result<(), AccountError> {
        macro_rules! spawn {
            ($field:ident) => {{
                let keys = context
                    .system()
                    .$field
                    .iter()
                    .map(|(key, _)| key.clone())
                    .collect::<Vec<_>>();
                for key in keys {
                    let connection = context
                        .system()
                        .$field
                        .remove(&key)
                        .expect("collected connection key")
                        .into_connection();
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
                    context.spawn_integration_account_events(key, connection);
                }
            }};
        }
        spawn!(binance_spot_user_websocket_connections);
        spawn!(binance_margin_user_websocket_connections);
        spawn!(binance_usdm_user_websocket_connections);
        spawn!(binance_coinm_user_websocket_connections);
        spawn!(binance_options_user_websocket_connections);
        spawn!(okx_private_websocket_connections);
        spawn!(ibkr_account_stream_connections);
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
        let system = context.system();
        let mut fetches = Vec::new();
        fetches.extend(
            fetch_accounts(
                &mut system.binance_spot_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.binance_funding_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.binance_margin_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.binance_usdm_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.binance_coinm_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.binance_options_rest_connections,
                &by_key,
                &resolver,
            )
            .await,
        );
        fetches.extend(
            fetch_accounts(&mut system.okx_private_rest_connections, &by_key, &resolver).await,
        );
        fetches.extend(
            fetch_accounts(
                &mut system.ibkr_account_query_connections,
                &by_key,
                &resolver,
            )
            .await,
        );

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
            }
            AccountRestRequest::MarkToMarket(value) => AccountRestResponse::MarkToMarket(
                MarkToMarket::try_from(value)
                    .map_err(AccountError::Invalid)
                    .and_then(|request| self.mark_to_market(request))
                    .map(|()| AccountCommandStatus {
                        status: "applied".into(),
                    })
                    .map_err(control_error),
            ),
            AccountRestRequest::AdvanceTime(value) => AccountRestResponse::AdvanceTime(
                self.advance_business_time(value.event_time_unix_nanos)
                    .map(|()| kairos_account_contract::AdvanceAccountTimeResponse {
                        event_time_unix_nanos: value.event_time_unix_nanos,
                    })
                    .map_err(control_error),
            ),
            AccountRestRequest::Refresh(value) => {
                let result = match parse_segments(value.segments) {
                    Ok(segments) => self
                        .refresh_from_system(context, segments.clone())
                        .await
                        .map(|()| self.refresh_response(segments)),
                    Err(error) => Err(error),
                };
                AccountRestResponse::Refresh(result.map_err(control_error))
            }
            AccountRestRequest::Reconcile(value) => {
                let result = match parse_segments(value.segments) {
                    Ok(segments) => self
                        .refresh_from_system(context, segments.clone())
                        .await
                        .map(|()| self.refresh_response(segments)),
                    Err(error) => Err(error),
                };
                AccountRestResponse::Reconcile(result.map_err(control_error))
            }
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
            .map(|value| value.account_id.to_string())
            .unwrap_or_default();
        kairos_account_contract::AccountRefreshResponse {
            status: "completed".into(),
            account_id,
            segments: segments
                .into_iter()
                .map(|value| value.to_string())
                .collect(),
        }
    }

    fn contract_health(&self) -> kairos_account_contract::Health {
        let ready = !self.conflux.segments.is_empty()
            && self.conflux.segments.values().all(SegmentSyncState::ready);
        kairos_account_contract::Health {
            status: if ready { "ready" } else { "degraded" }.into(),
            lease_valid: None,
            generation: self.generation(),
            event_sequence: self.event_sequence(),
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
            }
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
        let channel = (envelope.binding_id.clone(), envelope.channel_id.clone());
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
                    envelope.binding_id.clone(),
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
                source_id: format!("{}:{}", envelope.participant.id, envelope.binding_id),
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
            let key = (envelope.binding_id, envelope.channel_id, event_id);
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
            let Some(publisher) = context.system().aeron_publishers.get_mut(&event_key) else {
                break;
            };
            for (index, change) in event.changes.iter().enumerate() {
                let bytes = encode_business_change(
                    self.actor_id(),
                    &self.conflux.identity,
                    &event,
                    index,
                    change,
                )
                .map_err(AccountError::Publication)?;
                publisher
                    .resource_mut()
                    .publish(&bytes)
                    .map_err(|error| AccountError::Publication(error.to_string()))?;
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
        if let Some(writer) = context.system().mmap_writers.get_mut(&current_key) {
            let bytes = encode_account_current_view(self.actor_id(), &self.conflux.identity, &view)
                .map_err(AccountError::Publication)?;
            writer
                .resource_mut()
                .publish_with_metadata(metadata, &bytes)
                .map_err(|error| AccountError::Publication(error.to_string()))?;
        }
        let orders_key = OBSERVED_ORDERS.to_owned();
        if let Some(writer) = context
            .system()
            .account_view_publishers
            .get_mut(&orders_key)
        {
            let bytes =
                encode_observed_orders_current_view(self.actor_id(), &self.conflux.identity, &view)
                    .map_err(AccountError::Publication)?;
            writer
                .resource_mut()
                .publish(metadata, &bytes)
                .map_err(|error| AccountError::Publication(error.to_string()))?;
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
                }
                SegmentSyncLifecycle::Degraded => AccountSegmentSyncLifecycle::Degraded,
                SegmentSyncLifecycle::Resyncing => AccountSegmentSyncLifecycle::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentSyncLifecycle::Unavailable,
                SegmentSyncLifecycle::Stopped => AccountSegmentSyncLifecycle::Stopped,
            };
            segment.freshness = match state.lifecycle {
                SegmentSyncLifecycle::Live | SegmentSyncLifecycle::SnapshotCurrent => {
                    segment.freshness
                }
                SegmentSyncLifecycle::Degraded | SegmentSyncLifecycle::Stopped => {
                    AccountSegmentFreshness::Stale
                }
                SegmentSyncLifecycle::Resyncing => AccountSegmentFreshness::Resyncing,
                SegmentSyncLifecycle::Unavailable => AccountSegmentFreshness::Unavailable,
                SegmentSyncLifecycle::Configured | SegmentSyncLifecycle::Bootstrapping => {
                    AccountSegmentFreshness::Unknown
                }
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

async fn fetch_accounts<C: AccountQuery>(
    connections: &mut ManagedConnections<String, C>,
    segments: &BTreeMap<String, crate::domain::AccountSegment>,
    resolver: &AccountInstrumentResolver,
) -> Vec<RefreshFetch> {
    let mut fetches = Vec::new();
    for (key, managed) in connections.iter_mut() {
        let Some(segment) = segments.get(key).cloned() else {
            continue;
        };
        let started = Instant::now();
        let result = managed
            .connection_mut()
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

fn parse_segments(values: Vec<String>) -> Result<Vec<SegmentKey>, AccountError> {
    values
        .into_iter()
        .map(|value| {
            SegmentKey::new(value).map_err(|error| AccountError::Invalid(error.to_string()))
        })
        .collect()
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
        fill_id: FillId::new(value.fill_id)
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        order_id: value
            .order_id
            .map(kairos_primitives::OrderId::new)
            .transpose()
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        segment_key: SegmentKey::new(value.segment_key)
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        instrument_id: InstrumentId::new(value.instrument_id)
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        quantity: Quantity::new(value.quantity.mantissa, value.quantity.scale)
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        price: Price::new(value.price.mantissa, value.price.scale)
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        side: match value.side.trim().to_ascii_lowercase().as_str() {
            "buy" => OrderSide::Buy,
            "sell" => OrderSide::Sell,
            _ => {
                return Err(AccountError::Invalid(
                    "simulated settlement side must be buy or sell".into(),
                ))
            }
        },
        settlement_asset: value
            .settlement_asset
            .map(kairos_primitives::Currency::new)
            .transpose()
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        settlement_delta: value
            .settlement_delta
            .map(|amount| SignedQuantity::new(amount.mantissa, amount.scale))
            .transpose()
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        fee_asset: value
            .fee_asset
            .map(kairos_primitives::Currency::new)
            .transpose()
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        fee_amount: value
            .fee_amount
            .map(|amount| SignedQuantity::new(amount.mantissa, amount.scale))
            .transpose()
            .map_err(|error| AccountError::Invalid(error.to_string()))?,
        occurred_at_unix_nanos: value.occurred_at_unix_nanos.into(),
    })
}

fn validate_segment(expected: &SegmentKey, event: &ExternalAccountEvent) -> Result<(), String> {
    match event {
        ExternalAccountEvent::Snapshot(snapshot) if &snapshot.segment_key != expected => {
            Err(format!(
                "Account stream segment mismatch: expected {expected}, received {}",
                snapshot.segment_key
            ))
        }
        ExternalAccountEvent::Fill(fill) if &fill.segment_key != expected => Err(format!(
            "Account stream segment mismatch: expected {expected}, received {}",
            fill.segment_key
        )),
        ExternalAccountEvent::Batch(events) => {
            for event in events {
                validate_segment(expected, event)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}
