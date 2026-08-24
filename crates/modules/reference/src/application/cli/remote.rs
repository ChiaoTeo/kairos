//! Remote Reference control facade used by the CLI.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Reference server through the module contract. It must not be used by
//! standalone catalog queries.

use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, ReferenceSourceId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_reference_contract::{
    ReferenceControlRpcClient, ReferenceMutationResponse, ReferenceOptionCoverageResponse,
    ReferencePublishResponse, ReferenceRefreshResponse, ReferenceRuntimeStatusResponse,
    ReferenceSourceControlRequest, ReferenceSourceDefinitionRequest, ReferenceSourceStatusResponse,
    UpsertAssetRequest, UpsertInstrumentRequest, UpsertListingRequest,
};
use serde::Serialize;
use serde_json::Value;

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ReferenceProvidersResult {
    One(ReferenceProviderSummary),
    Many(Vec<ReferenceProviderSummary>),
}

#[derive(Debug, Serialize)]
pub struct ReferenceProviderSummary {
    pub source_id: String,
    pub provider_id: Option<Provider>,
    pub desired_state: Option<kairos_reference_contract::ReferenceSourceDesiredState>,
    pub sync_policy: Option<kairos_reference_contract::ReferenceSourceSyncPolicy>,
    pub phase: String,
    pub progress: String,
    pub pages_done: Option<u64>,
    pub pages_total: Option<u64>,
    pub records_seen: Option<u64>,
    pub records_changed: Option<u64>,
    pub work_item_id: Option<String>,
    pub scope_id: Option<String>,
    pub scope_kind: Option<String>,
    pub cursor_present: Option<bool>,
    pub stale: bool,
    pub last_known_good: bool,
    pub consecutive_failures: u32,
    pub last_success_unix_nanos: Option<UnixNanos>,
    pub last_attempt_unix_nanos: Option<UnixNanos>,
    pub retry_after_unix_nanos: Option<UnixNanos>,
    pub retry_backoff_seconds: Option<u64>,
}

#[derive(Debug, Serialize)]
pub struct ReferenceDoctorResult {
    pub status: String,
    pub app_phase: String,
    pub catalog_readiness: String,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub market_count: u64,
    pub source_count: usize,
    pub degraded_source_count: usize,
    pub pending_publication_count: u64,
    pub diagnostics: Vec<ReferenceDiagnosticSummary>,
}

#[derive(Debug, Serialize)]
pub struct ReferenceDiagnosticSummary {
    pub severity: String,
    pub code: String,
    pub source_id: Option<ReferenceSourceId>,
    pub message: String,
    pub next_action: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ReferenceCoverageResult {
    pub kind: &'static str,
    pub option_underlyings: Vec<InstrumentId>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedReferenceOutput {
    Status(ReferenceRuntimeStatusResponse),
    Refresh(ReferenceRefreshResponse),
    Publish(ReferencePublishResponse),
    OptionCoverage(ReferenceOptionCoverageResponse),
    SourceStatus(ReferenceSourceStatusResponse),
    Mutation(ReferenceMutationResponse),
    Providers(ReferenceProvidersResult),
    Doctor(ReferenceDoctorResult),
    Coverage(ReferenceCoverageResult),
    LocalCatalog(super::ReferenceCliOutput),
    Diagnostic(Value),
}

pub struct ConnectedReferenceApplication<C> {
    client: C,
}

impl<C> ConnectedReferenceApplication<C>
where
    C: ReferenceControlRpcClient,
{
    pub fn connect(client: C) -> Self {
        Self { client }
    }

    pub async fn runtime_status(
        &self,
    ) -> Result<ReferenceRuntimeStatusResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::status(&self.client).await?)
    }

    pub async fn status(
        &self,
    ) -> Result<ReferenceRuntimeStatusResponse, Box<dyn std::error::Error>> {
        self.runtime_status().await
    }

    pub async fn refresh(
        &self,
        source_id: Option<ReferenceSourceId>,
    ) -> Result<ReferenceRefreshResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::refresh(&self.client, source_id).await?)
    }

    pub async fn publish(&self) -> Result<ReferencePublishResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::publish(&self.client).await?)
    }

    pub async fn add_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> Result<ReferenceOptionCoverageResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::add_option_coverage(&self.client, underlying).await?)
    }

    pub async fn remove_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> Result<ReferenceOptionCoverageResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::remove_option_coverage(&self.client, underlying).await?)
    }

    pub async fn upsert_source_definition(
        &self,
        request: ReferenceSourceDefinitionRequest,
    ) -> Result<ReferenceSourceStatusResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::upsert_source_definition(&self.client, request).await?)
    }

    pub async fn set_source_desired_state(
        &self,
        request: ReferenceSourceControlRequest,
    ) -> Result<ReferenceSourceStatusResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::set_source_desired_state(&self.client, request).await?)
    }

    pub async fn upsert_asset(
        &self,
        request: UpsertAssetRequest,
    ) -> Result<ReferenceMutationResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::upsert_asset(&self.client, request).await?)
    }

    pub async fn upsert_instrument(
        &self,
        request: UpsertInstrumentRequest,
    ) -> Result<ReferenceMutationResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::upsert_instrument(&self.client, request).await?)
    }

    pub async fn upsert_listing(
        &self,
        request: UpsertListingRequest,
    ) -> Result<ReferenceMutationResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::upsert_listing(&self.client, request).await?)
    }

    pub fn summarize_providers(
        status: ReferenceRuntimeStatusResponse,
        source_filter: Option<String>,
        show: bool,
    ) -> Result<ReferenceProvidersResult, Box<dyn std::error::Error>> {
        let mut values = Vec::new();
        for source in status.sources {
            let source_id = source.source_id.as_str().to_owned();
            if let Some(expected) = source_filter.as_deref() {
                if source_id != expected {
                    continue;
                }
            }
            values.push(ReferenceProviderSummary {
                source_id,
                provider_id: source.provider_id,
                desired_state: source.desired_state,
                sync_policy: source.sync_policy,
                phase: source.phase.as_str().to_owned(),
                progress: source.progress.kind.as_str().to_owned(),
                pages_done: source.progress.pages_done,
                pages_total: source.progress.pages_total,
                records_seen: source.progress.records_seen,
                records_changed: source.progress.records_changed,
                work_item_id: source.work_item.work_item_id,
                scope_id: source.work_item.scope_id,
                scope_kind: source.work_item.scope_kind,
                cursor_present: source.work_item.cursor_present,
                stale: source.stale,
                last_known_good: source.has_last_known_good,
                consecutive_failures: source.consecutive_failures,
                last_success_unix_nanos: source.last_success_unix_nanos,
                last_attempt_unix_nanos: source.last_attempt_unix_nanos,
                retry_after_unix_nanos: source.retry_after_unix_nanos,
                retry_backoff_seconds: source.retry_backoff_seconds,
            });
        }
        if show {
            match values.as_slice() {
                [value] => Ok(ReferenceProvidersResult::One(ReferenceProviderSummary {
                    source_id: value.source_id.clone(),
                    provider_id: value.provider_id.clone(),
                    desired_state: value.desired_state,
                    sync_policy: value.sync_policy,
                    phase: value.phase.clone(),
                    progress: value.progress.clone(),
                    pages_done: value.pages_done,
                    pages_total: value.pages_total,
                    records_seen: value.records_seen,
                    records_changed: value.records_changed,
                    work_item_id: value.work_item_id.clone(),
                    scope_id: value.scope_id.clone(),
                    scope_kind: value.scope_kind.clone(),
                    cursor_present: value.cursor_present,
                    stale: value.stale,
                    last_known_good: value.last_known_good,
                    consecutive_failures: value.consecutive_failures,
                    last_success_unix_nanos: value.last_success_unix_nanos,
                    last_attempt_unix_nanos: value.last_attempt_unix_nanos,
                    retry_after_unix_nanos: value.retry_after_unix_nanos,
                    retry_backoff_seconds: value.retry_backoff_seconds,
                })),
                [] => Err(format!(
                    "unknown Reference source/provider: {}",
                    source_filter.expect("show has source filter")
                )
                .into()),
                _ => Err("Reference source/provider query is ambiguous".into()),
            }
        } else {
            Ok(ReferenceProvidersResult::Many(values))
        }
    }

    pub fn summarize_doctor(
        status: ReferenceRuntimeStatusResponse,
    ) -> Result<ReferenceDoctorResult, Box<dyn std::error::Error>> {
        let diagnostics = status
            .diagnostics
            .into_iter()
            .map(|diagnostic| {
                Ok(ReferenceDiagnosticSummary {
                    severity: diagnostic.severity.as_str().to_owned(),
                    code: diagnostic.code,
                    source_id: diagnostic.source_id,
                    message: diagnostic.message,
                    next_action: diagnostic.next_action,
                })
            })
            .collect::<Result<Vec<_>, Box<dyn std::error::Error>>>()?;
        Ok(ReferenceDoctorResult {
            status: status.status.as_str().to_owned(),
            app_phase: status.app_runtime.phase.as_str().to_owned(),
            catalog_readiness: status.catalog.readiness.as_str().to_owned(),
            generation: status.catalog.generation,
            event_sequence: status.catalog.event_sequence,
            market_count: status.catalog.market_count,
            source_count: status.sources.len(),
            degraded_source_count: status
                .sources
                .iter()
                .filter(|source| source.stale || source.consecutive_failures > 0)
                .count(),
            pending_publication_count: status.publication.pending_publication_count,
            diagnostics,
        })
    }

    pub fn summarize_coverage(status: ReferenceRuntimeStatusResponse) -> ReferenceCoverageResult {
        ReferenceCoverageResult {
            kind: "massive_options",
            option_underlyings: status.coverage.option_underlyings,
        }
    }
}
